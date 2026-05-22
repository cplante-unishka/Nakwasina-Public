#!/bin/bash

set -e

APP_NAME="Nakwasina-Public"
ZIP_URL="https://github.com/cplante-unishka/Nakwasina-Public/archive/refs/heads/main.zip"

PYTHON_VERSION_REQUIRED="3.14.5"
PYTHON_PKG="python-3.14.5-macos11.pkg"
PYTHON_URL="https://www.python.org/ftp/python/3.14.5/${PYTHON_PKG}"

INSTALL_DIR="/Library/${APP_NAME}"
TMP_DIR="/tmp/${APP_NAME}-install"
APP_SHORTCUT="/Applications/${APP_NAME}.command"

echo "Installing ${APP_NAME}..."

rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR"

##################################################
# CHECK PYTHON VERSION
##################################################

version_ge() {
    [ "$(printf '%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]
}

CURRENT_PYTHON_VERSION=""

if command -v python3 >/dev/null 2>&1; then
    CURRENT_PYTHON_VERSION=$(python3 --version | awk '{print $2}')
fi

echo "Detected Python version: ${CURRENT_PYTHON_VERSION:-None}"

if ! version_ge "$CURRENT_PYTHON_VERSION" "$PYTHON_VERSION_REQUIRED"; then

    echo ""
    echo "Python ${PYTHON_VERSION_REQUIRED}+ not found."
    echo "Downloading Python ${PYTHON_VERSION_REQUIRED}..."

    curl -L "$PYTHON_URL" -o "$TMP_DIR/$PYTHON_PKG"

    echo "Installing Python ${PYTHON_VERSION_REQUIRED}..."

    sudo installer -pkg "$TMP_DIR/$PYTHON_PKG" -target /

    echo "Python installation complete."

fi

##################################################
# LOCATE PYTHON 3.14
##################################################

if command -v python3.14 >/dev/null 2>&1; then
    PYTHON_BIN=$(command -v python3.14)
else
    echo "ERROR: python3.14 not found after installation."
    exit 1
fi

echo "Using Python: $PYTHON_BIN"

##################################################
# INSTALL APPLICATION
##################################################

echo "Downloading application..."

curl -L "$ZIP_URL" -o "$TMP_DIR/main.zip"

echo "Unzipping application..."

unzip -q "$TMP_DIR/main.zip" -d "$TMP_DIR"

echo "Installing application to ${INSTALL_DIR}..."

sudo rm -rf "$INSTALL_DIR"
sudo mkdir -p "$INSTALL_DIR"
sudo cp -R "$TMP_DIR/Nakwasina-Public-main/"* "$INSTALL_DIR"

##################################################
# INSTALL PYTHON REQUIREMENTS
##################################################

echo "Ensuring pip is available..."

"$PYTHON_BIN" -m ensurepip --upgrade

echo "Upgrading pip..."

"$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel

if [ -f "$INSTALL_DIR/requirements.txt" ]; then

    echo "Installing Python requirements..."

    "$PYTHON_BIN" -m pip install --upgrade -r "$INSTALL_DIR/requirements.txt"

else

    echo "No requirements.txt found."

fi

##################################################
# VERIFY INSTALL
##################################################

echo "Verifying installed packages..."

"$PYTHON_BIN" - <<EOF
import sys
print("Python executable:", sys.executable)
print("Python version:", sys.version)
EOF

##################################################
# CREATE APPLICATION SHORTCUT
##################################################

echo "Creating launcher shortcut..."

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

##################################################
# CLEANUP
##################################################

echo "Cleaning up..."

rm -rf "$TMP_DIR"

##################################################
# COMPLETE
##################################################

echo ""
echo "Installation complete."
echo ""
echo "Launch the application from:"
echo "$APP_SHORTCUT"
