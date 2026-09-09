"""
O'rnatgich uchun kirish nuqtasi.

Bu fayl faqat o'rnatadi va chiqadi - dasturning o'zini ishga
tushirmaydi. Dastur ichida alohida yengil Pult.exe bo'lib, o'rnatgich
uni joyiga qo'yadi. Shu sababli doimiy ishlaydigan dastur ichida
ffmpeg va cloudflared bo'lmaydi va kompyuter har yonganda ular
vaqtinchalik papkaga ochilmaydi.
"""
import multiprocessing
import os
import sys

if getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(sys.executable))

from pult import setup as setupmod  # noqa: E402


def main() -> int:
    if not setupmod.ask(
        "Pult shu kompyuterga o'rnatilsinmi?\n\n"
        "• dastur doimiy papkaga ko'chiriladi\n"
        "• kirganda o'zi ishga tushadi (terminal ochilmaydi)\n"
        "• telefondan ulanish uchun QR kod ochiladi\n\n"
        "Keyinroq o'chirish: Pult.exe --uninstall",
        "Pult o'rnatish"
    ):
        return 0

    ok, text = setupmod.install()
    if not ok:
        setupmod.message(text, "Pult o'rnatilmadi", 0x10)
        return 1

    setupmod.message(text + "\n\nEndi ishga tushiryapman.")
    setupmod.launch(setupmod.install_dir() / setupmod.EXE_NAME)
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
