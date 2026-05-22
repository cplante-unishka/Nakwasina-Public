#!/bin/bash

set -e

APP_NAME="Nakwasina-Public"
ZIP_URL="https://github.com/cplante-unishka/Nakwasina-Public/archive/refs/heads/main.zip"
INSTALL_DIR="/Library/${APP_NAME}"
TMP_DIR="/tmp/${APP_NAME}-install"
APP_SHORTCUT="/Applications/${APP_NAME}.command"

echo "Installing ${APP_NAME}..."

rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR"

echo "Downloading..."
curl -L "$ZIP_URL" -o "$TMP_DIR/main.zip"

echo "Unzipping..."
unzip -q "$TMP_DIR/main.zip" -d "$TMP_DIR"

echo "Installing to ${INSTALL_DIR}..."
sudo rm -rf "$INSTALL_DIR"
sudo mkdir -p "$INSTALL_DIR"
sudo cp -R "$TMP_DIR/Nakwasina-Public-main/"* "$INSTALL_DIR"

echo "Checking for Python 3.14..."

if command -v python3.14 >/dev/null 2>&1; then
    PYTHON_BIN=$(command -v python3.14)
else
    echo ""
    echo "ERROR: Python 3.14 is not installed."
    echo "Install it from:"
    echo "https://www.python.org/downloads/"
    exit 1
fi

echo "Using Python: $PYTHON_BIN"

echo "Ensuring pip is available..."
"$PYTHON_BIN" -m ensurepip --upgrade

echo "Upgrading pip..."
"$PYTHON_BIN" -m pip install --upgrade pip

echo "Installing requirements..."

if [ -f "$INSTALL_DIR/requirements.txt" ]; then
    "$PYTHON_BIN" -m pip install --upgrade -r "$INSTALL_DIR/requirements.txt"
else
    echo "No requirements.txt found."
fi

echo "Verifying installed packages are available..."

"$PYTHON_BIN" - <<EOF
import sys
import pkg_resources

print("Python executable:", sys.executable)
print("Installed packages verified.")
EOF

echo "Creating launcher shortcut in Applications..."

sudo tee "$APP_SHORTCUT" > /dev/null <<EOF
#!/bin/bash

export PATH="/usr/local/bin:/opt/homebrew/bin:\$PATH"

PYTHON_BIN=\$(command -v python3.14)

if [ -z "\$PYTHON_BIN" ]; then
    osascript -e 'display dialog "Python 3.14 is not installed." buttons {"OK"} default button "OK"'
    exit 1
fi

cd "$INSTALL_DIR"

"\$PYTHON_BIN" "$INSTALL_DIR/gui_app.py"
EOF

sudo chmod +x "$APP_SHORTCUT"

echo "Cleaning up..."
rm -rf "$TMP_DIR"

echo ""
echo "Installation complete."
echo "Application launcher created at:"
echo "$APP_SHORTCUT"
