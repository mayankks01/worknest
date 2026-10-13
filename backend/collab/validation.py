"""Content checks supplement malware scanning, never replace it."""

import re
import socket
import struct
import unicodedata
import warnings

from django.conf import settings
from PIL import Image
from pypdf import PdfReader

TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".txt": "text/plain",
}


def safe_name(value):
    name = unicodedata.normalize("NFKC", str(value)).replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    extension = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension not in TYPES:
        raise ValueError("Choose a PDF, PNG, JPEG or TXT file.")
    stem = name[: -len(extension)].strip(" .")[:120] or "attachment"
    return stem + extension, extension


def scan(content):
    # Fail closed on unavailable scanners, malformed replies and size-limit errors.
    with socket.create_connection((settings.CLAMAV_HOST, settings.CLAMAV_PORT), timeout=5) as sock:
        sock.settimeout(30)
        sock.sendall(b"zINSTREAM\0")
        content.seek(0)
        while chunk := content.read(65536):
            sock.sendall(struct.pack(">I", len(chunk)) + chunk)
        sock.sendall(struct.pack(">I", 0))
        reply = b""
        while b"\0" not in reply and len(reply) < 4096:
            part = sock.recv(4096)
            if not part:
                break
            reply += part
    if b"\0" not in reply:
        raise OSError("Scanner response was incomplete")
    result = reply.split(b"\0")[0]
    if result == b"stream: OK":
        return True
    if result.startswith(b"stream: ") and result.endswith(b" FOUND"):
        return False
    raise OSError("Scanner did not return a definitive result")


def validate(content, extension):
    content.seek(0)
    try:
        if extension in {".png", ".jpg", ".jpeg"}:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(content) as image:
                    expected = "PNG" if extension == ".png" else "JPEG"
                    if image.format != expected or image.width * image.height > 20000000:
                        raise ValueError("Unsupported image content")
                    image.verify()
        elif extension == ".pdf":
            if content.read(5) != b"%PDF-":
                raise ValueError("Invalid PDF header")
            content.seek(0)
            reader = PdfReader(content, strict=True)
            if reader.is_encrypted or not 1 <= len(reader.pages) <= 500:
                raise ValueError("Encrypted or oversized PDF structure")
            # Embedded files and launch/JavaScript actions are outside the supported format.
            root = reader.trailer["/Root"]
            if any(key in root for key in ["/OpenAction", "/AA", "/AcroForm"]):
                raise ValueError("Active PDF content is unsupported")
            names = root.get("/Names")
            if names and any(
                key in names.get_object() for key in ["/JavaScript", "/EmbeddedFiles"]
            ):
                raise ValueError("Embedded PDF content is unsupported")
            for page in reader.pages:
                if "/AA" in page or "/Annots" in page:
                    raise ValueError("Interactive PDF content is unsupported")
        else:
            text = content.read().decode("utf-8-sig")
            if not text or any(ord(char) < 32 and char not in "\t\r\n" for char in text):
                raise ValueError("Invalid plain text content")
    except Exception as exc:
        raise ValueError("File content does not match the supported format") from exc
    return TYPES[extension]
