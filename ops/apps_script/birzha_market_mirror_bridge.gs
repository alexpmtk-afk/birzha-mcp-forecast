/**
 * BIRZHA MCP -> Google Sheets market-data mirror bridge v1.
 *
 * Deploy as a Google Apps Script Web App that executes as the Drive owner.
 * The shared secret lives ONLY in Script Properties under
 * BIRZHA_MARKET_MIRROR_SECRET.  This bridge is hard-scoped to the existing
 * "Биржа/Архив рыночных данных" folder and refuses spreadsheets outside it.
 */
const BIRZHA_MARKET_ROOT_ID = '1A7IzjXYSCWReZXdtrPZgZgLTnFIifsT2';
const BIRZHA_MARKET_ROOT_NAME = 'Архив рыночных данных';
const SECRET_PROPERTY = 'BIRZHA_MARKET_MIRROR_SECRET';
const BRIDGE_VERSION = 1;
const ALLOWED_SHEETS = ['D1', 'SESSIONS', 'VERIFIED_RANGES', 'SYNC_STATUS'];
const MAX_ROWS_PER_SHEET = 25000;
const MAX_COLUMNS = 40;
const MAX_TOTAL_CELLS = 500000;

function json_(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}

function canonicalCell_(value) {
  if (value === null || typeof value === 'undefined') return '';
  if (value instanceof Date) return value.toISOString();
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  return String(value);
}

function digestRows_(rows) {
  const parts = [];
  rows.forEach(function(row) {
    parts.push(row.map(canonicalCell_).join('\u001f'));
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

function validateRows_(name, rows) {
  if (ALLOWED_SHEETS.indexOf(name) < 0) throw new Error('unsupported sheet: ' + name);
  if (!Array.isArray(rows) || rows.length < 1) throw new Error('sheet has no header: ' + name);
  if (rows.length > MAX_ROWS_PER_SHEET) throw new Error('sheet row limit exceeded: ' + name);
  const width = Array.isArray(rows[0]) ? rows[0].length : 0;
  if (width < 1 || width > MAX_COLUMNS) throw new Error('invalid sheet width: ' + name);
  rows.forEach(function(row) {
    if (!Array.isArray(row) || row.length !== width) throw new Error('non-rectangular sheet: ' + name);
  });
  return {rows: rows.length, columns: width, cells: rows.length * width};
}

function rootHealth_() {
  const root = DriveApp.getFolderById(BIRZHA_MARKET_ROOT_ID);
  if (root.getName() !== BIRZHA_MARKET_ROOT_NAME) {
    throw new Error('market root name mismatch');
  }
  return {
    ok: true,
    service: 'birzha-market-mirror-bridge',
    version: BRIDGE_VERSION,
    root_id: root.getId(),
    root_name: root.getName()
  };
}

function isFolderWithinRoot_(folder, depth) {
  if (folder.getId() === BIRZHA_MARKET_ROOT_ID) return true;
  if (depth > 12) return false;
  const parents = folder.getParents();
  while (parents.hasNext()) {
    if (isFolderWithinRoot_(parents.next(), depth + 1)) return true;
  }
  return false;
}

function assertSpreadsheetWithinRoot_(spreadsheetId) {
  if (!spreadsheetId) throw new Error('spreadsheet_id is required');
  const file = DriveApp.getFileById(String(spreadsheetId));
  const parents = file.getParents();
  while (parents.hasNext()) {
    if (isFolderWithinRoot_(parents.next(), 0)) return file;
  }
  throw new Error('spreadsheet is outside BIRZHA market-data root');
}

function writeSheetSnapshot_(ss, name, rows) {
  const meta = validateRows_(name, rows);
  const inputDigest = digestRows_(rows);
  const stageName = '__BIRZHA_STAGE_' + Utilities.getUuid().replace(/-/g, '').slice(0, 16);
  const stage = ss.insertSheet(stageName);
  try {
    stage.getRange(1, 1, meta.rows, meta.columns).setValues(rows);
    SpreadsheetApp.flush();
    const stagedValues = stage.getRange(1, 1, meta.rows, meta.columns).getValues();
    const stagedDigest = digestRows_(stagedValues);
    if (stagedDigest !== inputDigest) throw new Error('staging read-back mismatch for ' + name);

    let target = ss.getSheetByName(name);
    if (!target) target = ss.insertSheet(name);
    target.clearContents();
    target.getRange(1, 1, meta.rows, meta.columns).setValues(stagedValues);
    target.setFrozenRows(1);
    SpreadsheetApp.flush();

    const readBack = target.getRange(1, 1, meta.rows, meta.columns).getValues();
    const readBackDigest = digestRows_(readBack);
    if (readBackDigest !== inputDigest) throw new Error('target read-back mismatch for ' + name);

    return {
      rows_total: meta.rows,
      data_rows: Math.max(0, meta.rows - 1),
      columns: meta.columns,
      sha256: readBackDigest,
      first_data: meta.rows > 1 ? readBack[1].slice(0, Math.min(meta.columns, 10)).map(canonicalCell_) : [],
      last_data: meta.rows > 1 ? readBack[meta.rows - 1].slice(0, Math.min(meta.columns, 10)).map(canonicalCell_) : []
    };
  } finally {
    ss.deleteSheet(stage);
  }
}

function replaceSnapshot_(body) {
  const spreadsheetId = String(body.spreadsheet_id || '').trim();
  assertSpreadsheetWithinRoot_(spreadsheetId);
  const sheets = body.sheets;
  if (!sheets || typeof sheets !== 'object' || Array.isArray(sheets)) {
    throw new Error('sheets object is required');
  }

  const names = Object.keys(sheets);
  if (names.length < 1) throw new Error('empty sheets snapshot');
  let totalCells = 0;
  names.forEach(function(name) {
    totalCells += validateRows_(name, sheets[name]).cells;
  });
  if (totalCells > MAX_TOTAL_CELLS) throw new Error('snapshot cell limit exceeded');

  const ss = SpreadsheetApp.openById(spreadsheetId);
  const written = {};
  names.forEach(function(name) {
    written[name] = writeSheetSnapshot_(ss, name, sheets[name]);
  });
  return {
    ok: true,
    action: 'replace_snapshot',
    spreadsheet_id: spreadsheetId,
    parity: true,
    sheets: written,
    total_cells: totalCells
  };
}

function summary_(body) {
  const spreadsheetId = String(body.spreadsheet_id || '').trim();
  assertSpreadsheetWithinRoot_(spreadsheetId);
  const ss = SpreadsheetApp.openById(spreadsheetId);
  const result = {};
  ALLOWED_SHEETS.forEach(function(name) {
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
      sha256: digestRows_(values),
      first_data: lastRow > 1 ? values[1].slice(0, Math.min(lastColumn, 10)).map(canonicalCell_) : [],
      last_data: lastRow > 1 ? values[lastRow - 1].slice(0, Math.min(lastColumn, 10)).map(canonicalCell_) : []
    };
  });
  return {ok: true, action: 'summary', spreadsheet_id: spreadsheetId, sheets: result};
}

function doGet() {
  try {
    return json_(rootHealth_());
  } catch (err) {
    return json_({ok: false, error: String(err && err.message || err)});
  }
}

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    const expected = PropertiesService.getScriptProperties().getProperty(SECRET_PROPERTY);
    if (!expected || !body.secret || String(body.secret) !== expected) {
      return json_({ok: false, error: 'unauthorized'});
    }
    const action = String(body.action || '');
    if (action === 'health') return json_(rootHealth_());
    if (action === 'replace_snapshot') return json_(replaceSnapshot_(body));
    if (action === 'summary') return json_(summary_(body));
    return json_({ok: false, error: 'unsupported action'});
  } catch (err) {
    return json_({ok: false, error: String(err && err.message || err)});
  } finally {
    lock.releaseLock();
  }
}
