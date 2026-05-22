# Nakwasina Public

Nakwasina Public is a Python-based desktop application that can be installed on both **macOS** and **Windows** using the included installer scripts.

---

# Installation Guide

## macOS Installation

### Requirements

* macOS 11 or newer
* Internet connection
* Administrator password

### Steps

1. Download the macOS installer script from this repository:

   ```text
   install_nakwasina.sh
   ```

2. Open **Terminal**

3. Navigate to your Downloads folder:

   ```bash
   cd ~/Downloads
   ```

4. Make the installer executable:

   ```bash
   chmod +x install_nakwasina.sh
   ```

5. Run the installer:

   ```bash
   ./install_nakwasina.sh
   ```

6. Enter your macOS password when prompted.

---

### What the macOS installer does

The installer will automatically:

* Download and install Python 3.14.5 (if needed)

* Download the latest version of Nakwasina Public

* Install required Python packages

* Install the application into:

  ```text
  /Library/Nakwasina-Public
  ```

* Create an application launcher in:

  ```text
  /Applications/Nakwasina-Public.command
  ```

---

### Launching the Application on macOS

Open:

```text
Applications → Nakwasina-Public.command
```

You may need to right-click and choose:

```text
Open
```

the first time you run it because it was downloaded from the internet.

---

# Windows Installation

### Requirements

* Windows 10 or newer
* Internet connection
* Administrator permissions

### Steps

1. Download the Windows installer script from this repository:

   ```text
   install_nakwasina.ps1
   ```

2. Right-click the file and choose:

   ```text
   Run with PowerShell
   ```

OR

Open PowerShell as Administrator and run:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
.\install_nakwasina.ps1
```

---

### What the Windows installer does

The installer will automatically:

* Download and install Python 3.14.5 (if needed)

* Download the latest version of Nakwasina Public

* Install required Python packages

* Install the application into:

  ```text
  C:\Users\Public\Nakwasina-Public
  ```

* Create a Start Menu shortcut

---

### Launching the Application on Windows

Open the Start Menu and search for:

```text
Nakwasina-Public
```

---

# Updating

Re-run the installer script at any time to update the application.

---

# Troubleshooting

## macOS: “Developer cannot be verified”

Run:

```bash
xattr -cr /Applications/Nakwasina-Public.command
```

Then try opening the application again.

---

## Windows: PowerShell execution policy error

Run PowerShell as Administrator and execute:

```powershell
Set-ExecutionPolicy RemoteSigned
```

Then run the installer again.

---

# Repository Contents

| File                    | Purpose             |
| ----------------------- | ------------------- |
| `install_nakwasina.sh`  | macOS installer     |
| `install_nakwasina.ps1` | Windows installer   |
| `requirements.txt`      | Python dependencies |
| `gui_app.py`            | Main application    |

---

# Support

If you encounter installation issues, please open an issue in this repository.
