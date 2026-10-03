"""Classic Outlook draft integration with a mailto fallback.

Status detection must never activate Outlook: Dispatch can start the desktop app.
COM activation is therefore limited to explicit draft/send actions.
"""
import os
import shutil
from urllib.parse import quote, urlencode

def _classic_outlook_path():
    """Find an installed classic Outlook executable without launching it."""
    if os.name != "nt":
        return None
    try:
        import winreg
        subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\OUTLOOK.EXE"
        views = (0, getattr(winreg, "KEY_WOW64_32KEY", 0), getattr(winreg, "KEY_WOW64_64KEY", 0))
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in dict.fromkeys(views):
                try:
                    with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | view) as key:
                        value = winreg.QueryValueEx(key, "")[0]
                    path = os.path.expandvars(str(value).strip().strip('"'))
                    if os.path.isfile(path):
                        return path
                except OSError:
                    continue
    except ImportError:
        pass
    return shutil.which("OUTLOOK.EXE") or shutil.which("outlook.exe")

def detect():
    path = _classic_outlook_path()
    if path:
        return {"mode":"classic","available":True,"running":False,"path":path,"message":"Classic Outlook is installed. It will open only when you create a draft."}
    return {"mode":"mailto","available":False,"running":False,"message":"Classic Outlook was not found; email links will use your default mail app."}

def create_draft(to, subject, body, mode="draft", confirmed=False):
    if mode not in ("draft", "send"):
        raise ValueError("Mode must be 'draft' or 'send'.")
    if mode == "send" and not confirmed:
        raise PermissionError("Sending requires explicit confirmation after the countdown.")
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            app = win32com.client.Dispatch("Outlook.Application")
            mail = app.CreateItem(0)
            mail.To = to or ""
            mail.Subject = subject or ""
            mail.Body = body or ""
            if mode == "send":
                mail.Send()
                return {"mode":"classic","sent":True,"message":"Email sent through Outlook."}
            mail.Save()
            return {"mode":"classic","sent":False,"message":"Draft saved to Outlook Drafts."}
        finally:
            pythoncom.CoUninitialize()
    except PermissionError:
        raise
    except Exception as exc:
        if mode == "send":
            raise RuntimeError(f"Outlook could not send the message: {exc}") from exc
        # RFC 6068 mailto values use percent encoding. urllib's default form
        # encoding turns spaces into '+', which desktop mail clients show literally.
        uri = "mailto:?" + urlencode(
            {"to":to or "", "subject":subject or "", "body":body or ""},
            quote_via=quote,
        )
        try:
            os.startfile(uri)
            return {"mode":"mailto","sent":False,"message":"Opened a new email in your default mail app."}
        except Exception as fallback:
            raise RuntimeError(f"Could not create an Outlook draft or open a mail link: {fallback}") from exc
