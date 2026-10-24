#!/usr/bin/env python3
"""
DevDays Pune (16 Oct 2026) - send thank-you e-mails with each attendee's
certificate attached.

Folder layout (keep everything together):
    send_certificates.py
    recipients.csv          sr_no, name, branch, div, roll_no, contact, email, registration, status
    email_template.html     your HTML mail; "[Candidate Name]" is replaced
    certificates/           "<Attendee Name>.pdf" files
    .env                    optional: MLSC_EMAIL=... / MLSC_APP_PASSWORD=...

Usage:
    python send_certificates.py --dry-run               # builds .eml files in ./preview, sends nothing
    python send_certificates.py --test-to me@gmail.com   # sends ONE sample mail to yourself
    python send_certificates.py                          # sends to everyone

Login: use a Gmail *App Password* (Google Account > Security > 2-Step Verification
> App passwords). Either put MLSC_EMAIL / MLSC_APP_PASSWORD in a .env file next to
this script, set them as environment variables, or you will be prompted. Do NOT
use your normal Gmail password.
"""
import argparse, base64, csv, getpass, html, os, re, smtplib, ssl, sys, time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

# ------------------------------ SETTINGS ------------------------------------
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
SENDER_NAME = "MLSC NMIET, Pune"
SUBJECT = "Thank you for attending DevDays Pune - your certificate is attached"
DELAY_SECONDS = 3            # pause between mails (avoids Gmail rate limits)
PLACEHOLDER = "[Candidate Name]"
# ----------------------------------------------------------------------------

BASE = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(BASE, *a)


def load_env():
    """Optional: read MLSC_EMAIL / MLSC_APP_PASSWORD from a .env file next to the script."""
    path = P(".env")
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def load_recipients():
    """Read the CSV; one row per attendee (names here are already unique)."""
    people = []
    with open(P("recipients.csv"), newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = row["name"].strip()
            email = row.get("email", "").strip()
            people.append({"name": name, "email": email})
    return people


def load_template():
    """Return (html_with_cid_logo, logo_bytes, logo_subtype).
    Gmail/Outlook block base64 images, so an embedded logo (if any) is turned
    into an inline (cid:) attachment. The rest of the HTML is left untouched."""
    with open(P("email_template.html"), encoding="utf-8") as f:
        page = f.read()
    m = re.search(r'src="data:image/[a-zA-Z+.-]+;base64,([^"]+)"', page)
    if not m:
        return page, None, None
    raw = base64.b64decode(m.group(1))
    subtype = "png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else "gif" if raw[:3] == b"GIF" else "jpeg"
    page = page.replace(m.group(0), 'src="cid:event_logo"')
    return page, raw, subtype


def build_message(person, sender, page, logo, logo_type, cert_path):
    msg = EmailMessage()
    msg["Subject"] = SUBJECT
    msg["From"] = formataddr((SENDER_NAME, sender))
    msg["To"] = person["email"]
    msg["Message-ID"] = make_msgid()

    body_html = page.replace(PLACEHOLDER, html.escape(person["name"]))
    msg.set_content(
        f"Dear {person['name']},\n\n"
        "Thank you for attending the Github Dev Days, held in "
        "Pune, India on October 16, 2026, and for completing the GitHub and GitHub "
        "Copilot sessions with us.\n\n"
        "We hope you enjoyed the sessions and picked up some new skills along the way. "
        "Your certificate of completion is attached to this email as a PDF.\n\n"
        "We look forward to seeing you at future events!\n\n"
        "Regards\nMicrosoft Student Ambassadors - DevDays Pune\n"
    )
    msg.add_alternative(body_html, subtype="html")
    if logo:
        msg.get_payload()[1].add_related(logo, "image", logo_type, cid="<event_logo>")
    with open(cert_path, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="pdf",
                           filename=os.path.basename(cert_path))
    return msg


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="save .eml previews, send nothing")
    ap.add_argument("--test-to", metavar="EMAIL", help="send only the first mail, to this address")
    args = ap.parse_args()

    page, logo, logo_type = load_template()
    if PLACEHOLDER not in page:
        sys.exit(f'"{PLACEHOLDER}" not found in email_template.html')

    people = load_recipients()
    todo, skipped = [], []
    for p in people:
        cert = P("certificates", p["name"] + ".pdf")
        if not p["email"]:
            skipped.append((p["name"], "no email address"))
        elif not os.path.isfile(cert):
            skipped.append((p["name"], "certificate PDF not found"))
        else:
            todo.append((p, cert))

    print(f"{len(people)} attendees | {len(todo)} to mail | {len(skipped)} skipped")
    for n, why in skipped:
        print(f"  - skipped: {n} ({why})")

    if args.dry_run:
        os.makedirs(P("preview"), exist_ok=True)
        for p, cert in todo:
            msg = build_message(p, "sender@example.com", page, logo, logo_type, cert)
            with open(P("preview", p["name"] + ".eml"), "wb") as f:
                f.write(bytes(msg))
        print("Dry run done. Open the files in ./preview to check them.")
        return

    sender = os.environ.get("MLSC_EMAIL") or input("Sender Gmail address: ").strip()
    password = os.environ.get("MLSC_APP_PASSWORD") or getpass.getpass("Gmail App Password: ")

    log_path = P("sent_log.csv")
    already = set()
    if os.path.isfile(log_path) and not args.test_to:
        with open(log_path, newline="", encoding="utf-8") as f:
            already = {r["email"].lower() for r in csv.DictReader(f) if r["status"] == "sent"}
    new_log = not os.path.isfile(log_path)

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as server, \
         open(log_path, "a", newline="", encoding="utf-8") as logf:
        server.login(sender, password)
        log = csv.writer(logf)
        if new_log:
            log.writerow(["time", "name", "email", "status", "detail"])
        sent = failed = 0
        for p, cert in todo:
            if p["email"].lower() in already:
                print(f"already sent, skipping: {p['name']}")
                continue
            msg = build_message(p, sender, page, logo, logo_type, cert)
            if args.test_to:
                del msg["To"]; msg["To"] = args.test_to
            try:
                server.send_message(msg)
                status, detail = "sent", ""
                sent += 1
                print(f"sent  -> {p['name']} <{msg['To']}>")
            except Exception as e:
                status, detail = "failed", str(e)
                failed += 1
                print(f"FAILED -> {p['name']}: {e}")
            if not args.test_to:
                log.writerow([datetime.now().isoformat(timespec="seconds"),
                              p["name"], p["email"], status, detail])
                logf.flush()
            if args.test_to:
                break
            time.sleep(DELAY_SECONDS)
        print(f"Finished. sent={sent} failed={failed}")


if __name__ == "__main__":
    main()
