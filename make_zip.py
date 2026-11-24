"""Собирает function.zip для загрузки в Yandex Cloud Functions: python make_zip.py"""

import glob
import os
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
FILES = ["index.py"] + [os.path.relpath(p, os.path.join(ROOT, "function"))
                        for p in glob.glob(os.path.join(ROOT, "function", "certs", "*"))]

with zipfile.ZipFile(os.path.join(ROOT, "function.zip"), "w", zipfile.ZIP_DEFLATED) as z:
    for rel in FILES:
        z.write(os.path.join(ROOT, "function", rel), rel.replace(os.sep, "/"))
        print("  +", rel.replace(os.sep, "/"))
print("Готово: function.zip")
