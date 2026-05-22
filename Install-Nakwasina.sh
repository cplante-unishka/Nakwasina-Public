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

echo "Installing Python requirements..."
if [ -f "$INSTALL_DIR/requirements.txt" ]; then
    python3 -m pip install -r "$INSTALL_DIR/requirements.txt"
else
    echo "No requirements.txt found."
fi

echo "Creating launcher shortcut in Applications..."

sudo tee "$APP_SHORTCUT" > /dev/null <<EOF
#!/bin/bash
cd "$INSTALL_DIR"
python3 "$INSTALL_DIR/gui_app.py"
EOF

sudo chmod +x "$APP_SHORTCUT"

echo "Cleaning up..."
rm -rf "$TMP_DIR"

echo ""
echo "Installation complete."
echo "Launch the app from:"
echo "$APP_SHORTCUT"