#!/bin/bash
set -e

APP_NAME="Nakwasina-Public"
ZIP_URL="https://github.com/cplante-unishka/Nakwasina-Public/archive/refs/heads/main.zip"

PYTHON_REQUIRED="3.14.5"
PYTHON_PKG="python-3.14.5-macos11.pkg"
PYTHON_URL="https://www.python.org/ftp/python/3.14.5/${PYTHON_PKG}"
PYTHON_BIN="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14"

INSTALL_DIR="/Library/${APP_NAME}"
TMP_DIR="/tmp/${APP_NAME}-install"
APP_SHORTCUT="/Applications/${APP_NAME}.command"

version_at_least() {
    "$1" - <<EOF
from packaging.version import Version
import sys
sys.exit(0 if Version("$2") >= Version("$3") else 1)
EOF
}

install_python() {
    echo "Downloading Python ${PYTHON_REQUIRED}..."
    curl -fL "$PYTHON_URL" -o "$TMP_DIR/$PYTHON_PKG"

    echo "Installing Python ${PYTHON_REQUIRED}..."
    sudo installer -pkg "$TMP_DIR/$PYTHON_PKG" -target /
}

rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR"

echo "Checking Python ${PYTHON_REQUIRED}+..."

NEED_PYTHON_INSTALL=1

if [ -x "$PYTHON_BIN" ]; then
    CURRENT_VERSION=$("$PYTHON_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')
    echo "Found Python $CURRENT_VERSION at $PYTHON_BIN"

    if "$PYTHON_BIN" - <<EOF
import sys
sys.exit(0 if sys.version_info >= (3,14,5) else 1)
EOF
    then
        NEED_PYTHON_INSTALL=0
    fi
fi

if [ "$NEED_PYTHON_INSTALL" -eq 1 ]; then
    install_python
fi

if [ ! -x "$PYTHON_BIN" ]; then
    echo "ERROR: Python 3.14 was not installed correctly."
    exit 1
fi

echo "Using Python: $PYTHON_BIN"

echo "Downloading application..."
curl -fL "$ZIP_URL" -o "$TMP_DIR/main.zip"

echo "Unzipping..."
unzip -q "$TMP_DIR/main.zip" -d "$TMP_DIR"

echo "Installing to $INSTALL_DIR..."
sudo rm -rf "$INSTALL_DIR"
sudo mkdir -p "$INSTALL_DIR"
sudo cp -R "$TMP_DIR/Nakwasina-Public-main/"* "$INSTALL_DIR"

echo "Installing requirements..."
"$PYTHON_BIN" -m ensurepip --upgrade
"$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel

if [ -f "$INSTALL_DIR/requirements.txt" ]; then
    "$PYTHON_BIN" -m pip install --upgrade -r "$INSTALL_DIR/requirements.txt"
fi

echo "Creating Applications shortcut..."
sudo tee "$APP_SHORTCUT" > /dev/null <<EOF
#!/bin/bash
export PATH="/Library/Frameworks/Python.framework/Versions/3.14/bin:/usr/local/bin:/opt/homebrew/bin:\$PATH"

PYTHON_BIN="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14"

if [ ! -x "\$PYTHON_BIN" ]; then
    osascript -e 'display dialog "Python 3.14.5 is not installed correctly." buttons {"OK"} default button "OK"'
    exit 1
fi

cd "$INSTALL_DIR"
"\$PYTHON_BIN" "$INSTALL_DIR/gui_app.py"
EOF

sudo chmod +x "$APP_SHORTCUT"

rm -rf "$TMP_DIR"

echo "Installation complete."
echo "Launch from: $APP_SHORTCUT"
