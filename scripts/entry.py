"""
.exe uchun kirish nuqtasi.

PyInstaller modul emas, fayl kutadi - shuning uchun `python -m pult`
o'rniga shu kichik fayl ishlatiladi.
"""
import multiprocessing
import os
import sys

if getattr(sys, "frozen", False):
    # Yig'ilgan .exe ichida modul yo'li o'zgaradi
    sys.path.insert(0, os.path.dirname(sys.executable))

from pult.__main__ import main  # noqa: E402

if __name__ == "__main__":
    # Windows'da jarayon nusxalanganda dastur qayta ishga tushib
    # ketmasligi uchun
    multiprocessing.freeze_support()
    sys.exit(main())
