@echo off
echo [1/3] Installing required packages...
pip install -r requirements.txt
pip install pyinstaller

echo.
echo [2/3] Building the .exe with PyInstaller (this can take a few minutes)...
pyinstaller --onefile --name AdCreativeClassifier --add-data "templates;templates" --collect-all pymupdf --collect-submodules skimage --hidden-import=PIL._tkinter_finder app.py

echo.
echo [3/3] Build complete! The exe file was created at:
echo   dist\AdCreativeClassifier.exe
echo.
echo Send just that one exe file to others - they do not need Python installed.
pause
