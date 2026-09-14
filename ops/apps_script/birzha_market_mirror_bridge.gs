/**
 * BIRZHA addon for the existing Marketplaces MCP -> Google Drive bridge.
 *
 * IMPORTANT:
 * - This file is added to the EXISTING Apps Script project/Web App.
 * - It deliberately defines NO doGet/doPost and NO separate secret.
 * - The existing doPost authenticates MCP_DRIVE_BRIDGE_SECRET first, then
 *   delegates only birzha_* actions to handleBirzhaAction_().
 * - All Birzha access is hard-scoped to the established
 *   "Биржа/Архив рыночных данных" hierarchy.
 */
const BIRZHA_MARKET_ROOT_ID = '1A7IzjXYSCWReZXdtrPZgZgLTnFIifsT2';
const BIRZHA_MARKET_ROOT_NAME = 'Архив рыночных данных';
const BIRZHA_BRIDGE_VERSION = 3;
const BIRZHA_ALLOWED_SHEETS = ['D1', 'SESSIONS', 'VERIFIED_RANGES', 'SYNC_STATUS'];
const BIRZHA_MAX_ROWS_PER_SHEET = 25000;
const BIRZHA_MAX_COLUMNS = 40;
const BIRZHA_MAX_TOTAL_CELLS = 500000;

const BIRZHA_INSTRUMENT_TARGETS = {
  'SI': {
    folderId: '1c_WRQl2pPfsDSnwcR1l2dGyr0jwQVoNF',
    folderName: 'Si — Доллар-рубль',
    archiveCode: 'Si'
  },
  'BR': {
    folderId: '1X9BbkWE-OeISlXKuPQ1SW6uT593pHwot',
    folderName: 'BR — Brent',
    archiveCode: 'BR',
    spreadsheetId: '1y8nMiqmMKv1af_lRQjGPCYDKpz3P1nfojkPjqqa4JjQ',
    spreadsheetName: 'BIRZHA — BR — Market Data Mirror'
  },
  'GOLD': {
    folderId: '1mpXF423F5Y7oiSKSSGf5YmmbFZMcXohx',
    folderName: 'GOLD — Золото',
    archiveCode: 'GOLD'
  },
  'IMOEX': {
    folderId: '1gDvLdbPe1vdrpP5hFUTNNetmxhkzIJYF',
    folderName: 'MIX_MX — Индекс МосБиржи',
    archiveCode: 'MIX_MX'
  },
  'RTSI': {
    folderId: '1v0ZYWbxp5vuH8WIuQtTcg6qBfX1b69Ez',
    folderName: 'RTS — Индекс РТС',
    archiveCode: 'RTS'
  },
  'SBER': {
    folderId: '14WYHet60gDRUtxlLAJ-BX_8_dyKiPjh0',
    folderName: 'SBER — Сбербанк',
    archiveCode: 'SBER'
  },
  'TATN': {
    folderId: '1LPKvOALglvRInQuTKmJhVmbF-dAxpI61',
    folderName: 'TATN — Татнефть',
    archiveCode: 'TATN'
  }
};

/**
 * Called only AFTER the existing shared doPost has validated its shared secret.
 * Returns null for every non-Birzha action, preserving Marketplace behavior.
 */
function handleBirzhaAction_(action, body) {
  const normalized = String(action || '').trim().toLowerCase();
  if (normalized === 'birzha_health') return birzhaRootHealth_();
  if (normalized === 'birzha_ensure_archive') return birzhaEnsureArchive_(body || {});
  if (normalized === 'birzha_replace_snapshot') return birzhaReplaceSnapshot_(body || {});
  if (normalized === 'birzha_summary') return birzhaSummary_(body || {});
  return null;
}

function birzhaCanonicalCell_(value) {
  if (value === null || typeof value === 'undefined') return '';
  if (value instanceof Date) return value.toISOString();
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  return String(value);
}

function birzhaDigestRows_(rows) {
  const parts = [];
  rows.forEach(function(row) {
    parts.push(row.map(birzhaCanonicalCell_).join('\u001f'));
  });
  const bytes = Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256,
    parts.join('\u001e'),
    Utilities.Charset.UTF_8
  );
  return bytes.map(function(b) {
    const value = b < 0 ? b + 256 : b;
    return ('0' + value.toString(16)).slice(-2);
  }).join('');
}

function birzhaValidateRows_(name, rows) {
  if (BIRZHA_ALLOWED_SHEETS.indexOf(name) < 0) throw new Error('unsupported Birzha sheet: ' + name);
  if (!Array.isArray(rows) || rows.length < 1) throw new Error('Birzha sheet has no header: ' + name);
  if (rows.length > BIRZHA_MAX_ROWS_PER_SHEET) throw new Error('Birzha sheet row limit exceeded: ' + name);
  const width = Array.isArray(rows[0]) ? rows[0].length : 0;
  if (width < 1 || width > BIRZHA_MAX_COLUMNS) throw new Error('invalid Birzha sheet width: ' + name);
  rows.forEach(function(row) {
    if (!Array.isArray(row) || row.length !== width) throw new Error('non-rectangular Birzha sheet: ' + name);
  });
  return {rows: rows.length, columns: width, cells: rows.length * width};
}

function birzhaRootHealth_() {
  const root = DriveApp.getFolderById(BIRZHA_MARKET_ROOT_ID);
  if (root.getName() !== BIRZHA_MARKET_ROOT_NAME) throw new Error('Birzha market root name mismatch');
  return {
    ok: true,
    service: 'shared-drive-bridge-birzha-addon',
    version: BIRZHA_BRIDGE_VERSION,
    root_id: root.getId(),
    root_name: root.getName()
  };
}

function birzhaIsFolderWithinRoot_(folder, depth) {
  if (folder.getId() === BIRZHA_MARKET_ROOT_ID) return true;
  if (depth > 12) return false;
  const parents = folder.getParents();
  while (parents.hasNext()) {
    if (birzhaIsFolderWithinRoot_(parents.next(), depth + 1)) return true;
  }
  return false;
}

function birzhaNormalizeSymbol_(symbol) {
  const value = String(symbol || '').trim().toUpperCase();
  if (!value) throw new Error('Birzha symbol is required');
  return value;
}

function birzhaTargetForSymbol_(symbol) {
  const key = birzhaNormalizeSymbol_(symbol);
  const target = BIRZHA_INSTRUMENT_TARGETS[key];
  if (!target) throw new Error('unsupported Birzha archive symbol: ' + key);
  const folder = DriveApp.getFolderById(target.folderId);
  if (folder.getName() !== target.folderName) throw new Error('Birzha instrument folder name mismatch for ' + key);
  if (!birzhaIsFolderWithinRoot_(folder, 0)) throw new Error('Birzha instrument folder is outside market-data root');
  return {key: key, target: target, folder: folder};
}

function birzhaFileHasDirectParent_(file, folderId) {
  const parents = file.getParents();
  while (parents.hasNext()) {
    if (parents.next().getId() === folderId) return true;
  }
  return false;
}

function birzhaAssertSpreadsheetInTarget_(spreadsheetId, targetFolderId) {
  if (!spreadsheetId) throw new Error('Birzha spreadsheet_id is required');
  const file = DriveApp.getFileById(String(spreadsheetId));
  if (file.getMimeType() !== MimeType.GOOGLE_SHEETS) throw new Error('Birzha target is not a Google Sheet');
  if (!birzhaFileHasDirectParent_(file, String(targetFolderId))) {
    throw new Error('Birzha spreadsheet is outside expected instrument folder');
  }
  return file;
}

function birzhaAssertSpreadsheetWithinRoot_(spreadsheetId) {
  if (!spreadsheetId) throw new Error('Birzha spreadsheet_id is required');
  const file = DriveApp.getFileById(String(spreadsheetId));
  if (file.getMimeType() !== MimeType.GOOGLE_SHEETS) throw new Error('Birzha target is not a Google Sheet');
  const parents = file.getParents();
  while (parents.hasNext()) {
    if (birzhaIsFolderWithinRoot_(parents.next(), 0)) return file;
  }
  throw new Error('Birzha spreadsheet is outside market-data root');
}

function birzhaEnsureArchive_(body) {
  const resolved = birzhaTargetForSymbol_(body.symbol);
  let file = null;
  let created = false;

  // Never duplicate a known canonical archive. BR already exists and is filled.
  if (resolved.target.spreadsheetId) {
    file = birzhaAssertSpreadsheetInTarget_(resolved.target.spreadsheetId, resolved.folder.getId());
    if (resolved.target.spreadsheetName && file.getName() !== resolved.target.spreadsheetName) {
      throw new Error('Birzha canonical spreadsheet name mismatch for ' + resolved.key);
    }
  } else {
    const title = 'MOEX_HISTDATA_' + resolved.target.archiveCode + '_MCP_CANONICAL';
    const files = resolved.folder.getFilesByName(title);
    while (files.hasNext()) {
      const candidate = files.next();
      if (candidate.getMimeType() !== MimeType.GOOGLE_SHEETS) continue;
      if (file !== null) throw new Error('duplicate Birzha canonical market archive: ' + title);
      file = candidate;
    }
    if (file === null) {
      const spreadsheet = SpreadsheetApp.create(title);
      file = DriveApp.getFileById(spreadsheet.getId());
      file.moveTo(resolved.folder);
      created = true;
    }
    birzhaAssertSpreadsheetInTarget_(file.getId(), resolved.folder.getId());
  }

  return {
    ok: true,
    action: 'birzha_ensure_archive',
    symbol: resolved.key,
    archive_code: resolved.target.archiveCode,
    folder_id: resolved.folder.getId(),
    folder_name: resolved.folder.getName(),
    spreadsheet_id: file.getId(),
    spreadsheet_name: file.getName(),
    created: created
  };
}

function birzhaWriteSheetSnapshot_(ss, name, rows) {
  const meta = birzhaValidateRows_(name, rows);
  const inputDigest = birzhaDigestRows_(rows);
  const stageName = '__BIRZHA_STAGE_' + Utilities.getUuid().replace(/-/g, '').slice(0, 16);
  const stage = ss.insertSheet(stageName);
  try {
    stage.getRange(1, 1, meta.rows, meta.columns).setValues(rows);
    SpreadsheetApp.flush();
    const stagedValues = stage.getRange(1, 1, meta.rows, meta.columns).getValues();
    if (birzhaDigestRows_(stagedValues) !== inputDigest) throw new Error('Birzha staging read-back mismatch for ' + name);

    let target = ss.getSheetByName(name);
    if (!target) target = ss.insertSheet(name);
    target.clearContents();
    target.getRange(1, 1, meta.rows, meta.columns).setValues(stagedValues);
    target.setFrozenRows(1);
    SpreadsheetApp.flush();

    const readBack = target.getRange(1, 1, meta.rows, meta.columns).getValues();
    const readBackDigest = birzhaDigestRows_(readBack);
    if (readBackDigest !== inputDigest) throw new Error('Birzha target read-back mismatch for ' + name);

    return {
      rows_total: meta.rows,
      data_rows: Math.max(0, meta.rows - 1),
      columns: meta.columns,
      sha256: readBackDigest,
      first_data: meta.rows > 1 ? readBack[1].slice(0, Math.min(meta.columns, 10)).map(birzhaCanonicalCell_) : [],
      last_data: meta.rows > 1 ? readBack[meta.rows - 1].slice(0, Math.min(meta.columns, 10)).map(birzhaCanonicalCell_) : []
    };
  } finally {
    ss.deleteSheet(stage);
  }
}

function birzhaReplaceSnapshot_(body) {
  const spreadsheetId = String(body.spreadsheet_id || '').trim();
  birzhaAssertSpreadsheetWithinRoot_(spreadsheetId);
  const sheets = body.sheets;
  if (!sheets || typeof sheets !== 'object' || Array.isArray(sheets)) throw new Error('Birzha sheets object is required');

  const names = Object.keys(sheets);
  if (names.length < 1) throw new Error('empty Birzha sheets snapshot');
  let totalCells = 0;
  names.forEach(function(name) { totalCells += birzhaValidateRows_(name, sheets[name]).cells; });
  if (totalCells > BIRZHA_MAX_TOTAL_CELLS) throw new Error('Birzha snapshot cell limit exceeded');

  const ss = SpreadsheetApp.openById(spreadsheetId);
  const written = {};
  names.forEach(function(name) { written[name] = birzhaWriteSheetSnapshot_(ss, name, sheets[name]); });
  return {
    ok: true,
    action: 'birzha_replace_snapshot',
    spreadsheet_id: spreadsheetId,
    parity: true,
    sheets: written,
    total_cells: totalCells
  };
}

function birzhaSummary_(body) {
  const spreadsheetId = String(body.spreadsheet_id || '').trim();
  birzhaAssertSpreadsheetWithinRoot_(spreadsheetId);
  const ss = SpreadsheetApp.openById(spreadsheetId);
  const result = {};
  BIRZHA_ALLOWED_SHEETS.forEach(function(name) {
    const sheet = ss.getSheetByName(name);
    if (!sheet) return;
    const lastRow = sheet.getLastRow();
    const lastColumn = sheet.getLastColumn();
    if (lastRow < 1 || lastColumn < 1) {
      result[name] = {rows_total: 0, data_rows: 0, columns: 0, sha256: ''};
      return;
    }
    const values = sheet.getRange(1, 1, lastRow, lastColumn).getValues();
    result[name] = {
      rows_total: lastRow,
      data_rows: Math.max(0, lastRow - 1),
      columns: lastColumn,
      sha256: birzhaDigestRows_(values),
      first_data: lastRow > 1 ? values[1].slice(0, Math.min(lastColumn, 10)).map(birzhaCanonicalCell_) : [],
      last_data: lastRow > 1 ? values[lastRow - 1].slice(0, Math.min(lastColumn, 10)).map(birzhaCanonicalCell_) : []
    };
  });
  return {ok: true, action: 'birzha_summary', spreadsheet_id: spreadsheetId, sheets: result};
}
