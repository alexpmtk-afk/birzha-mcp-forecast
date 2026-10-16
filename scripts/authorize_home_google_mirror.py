"""Interactive Google desktop OAuth bootstrap, run only after owner authorisation.

Never run automatically on server startup. Requires a downloaded desktop client
configuration and Google's explicit consent in the owner's system browser.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import secrets
import sys
import webbrowser
from urllib.parse import parse_qs, urlencode, urlparse
import httpx

SCOPES=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.metadata.readonly"]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--client-file",required=True)
    parser.add_argument("--credential-output",required=True)
    args=parser.parse_args()
    target=Path(args.credential_output)
    if target.exists(): raise RuntimeError("Output already exists; existing credentials will not be overwritten")
    if not target.parent.is_dir(): raise RuntimeError("Create an access-restricted local credential directory first")
    cfg=json.loads(Path(args.client_file).read_text(encoding="utf-8"))["installed"]
    if not cfg.get("client_id") or not cfg.get("client_secret"):raise RuntimeError("Google desktop client configuration required")
    state=secrets.token_urlsafe(32); verifier=secrets.token_urlsafe(64)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    answer={}
    class Callback(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            parsed=urlparse(self.path);params=parse_qs(parsed.query)
            valid=(parsed.path=="/oauth2callback" and params.get("state")==[state] and params.get("code") and not params.get("error"))
            if valid: answer["code"]=params["code"][0]
            else: answer["error"]=True
            self.send_response(200 if valid else 400);self.send_header("Content-Type","text/plain; charset=utf-8");self.end_headers()
            self.wfile.write(b"Authorisation received. You can close this browser tab." if valid else b"Authorisation refused. No credentials saved.")
    with HTTPServer(("127.0.0.1",0),Callback) as listener:
        listener.timeout=180
        redirect=f"http://127.0.0.1:{listener.server_port}/oauth2callback"
        url="https://accounts.google.com/o/oauth2/v2/auth?"+urlencode({"client_id":cfg["client_id"],"redirect_uri":redirect,"response_type":"code","scope":" ".join(SCOPES),"state":state,"code_challenge":challenge,"code_challenge_method":"S256","access_type":"offline","prompt":"consent"})
        print("Откроется Google: разрешите этой программе обновлять таблицы и читать сведения об их расположении. Секреты в чат не отправляйте.")
        if not webbrowser.open(url):raise RuntimeError("System browser did not open")
        listener.handle_request()
    if not answer.get("code"):raise RuntimeError("No valid owner consent; credentials were not saved")
    with httpx.Client(timeout=30,follow_redirects=False) as client:
        response=client.post("https://oauth2.googleapis.com/token",data={"grant_type":"authorization_code","client_id":cfg["client_id"],"client_secret":cfg["client_secret"],"code":answer["code"],"code_verifier":verifier,"redirect_uri":redirect})
    if not response.is_success:raise RuntimeError("Google refused code exchange; credentials were not saved")
    data=response.json()
    if not data.get("refresh_token") or not set(SCOPES).issubset(str(data.get("scope","")).split()):raise RuntimeError("Required offline consent was not granted")
    saved={"client_id":cfg["client_id"],"client_secret":cfg["client_secret"],"refresh_token":data["refresh_token"]}
    # Exclusive creation: never replace credentials of another participant.
    with target.open("x",encoding="utf-8") as output:json.dump(saved,output)
    print("Доступ для фонового обновления сохранён локально. Значения доступа не выведены.")

if __name__=="__main__":
    try:main()
    except Exception:
        print("Настройка не завершена. Проверьте тип приложения Google, согласие владельца и защищённую папку назначения. Секреты не выводились.",file=sys.stderr)
        raise SystemExit(1)