# Remote Management Tool - All-in-One Manager

This script provides a unified interface for configuring, building, and managing the remote monitoring and administration tool.

## Features

- **Automatic Interface Detection**: Detects GUI availability and uses GUI when possible, CLI when not
- **Server Management**: Start/stop the C2 server with custom configuration
- **Agent Building**: Compile standalone executables with custom settings
- **Cross-Platform**: Works on Windows, Linux, and macOS
- **Configuration Persistence**: Saves settings between runs

## Usage

### Basic Usage

```bash
# Run with automatic interface detection
python remote_manager.py

# Force CLI mode
python remote_manager.py --cli

# Force GUI mode (if available)
python remote_manager.py --gui
```

### CLI Interface

When GUI is not available or CLI mode is forced, the script provides an interactive menu:

1. **Configure Server Connection** - Set server IP, port, icon, and PDF decoy
2. **Start Server** - Launch the C2 server
3. **Stop Server** - Stop the running server
4. **Build Standalone Agent** - Create executable with current configuration
5. **Show Current Configuration** - Display current settings
6. **Exit** - Quit the program

### Configuration Options

- **Server IP**: IP address for the C2 server (default: 127.0.0.1)
- **Server Port**: Port for the C2 server (default: 8080)
- **Icon Path**: Custom icon file for the built executable
- **PDF Decoy**: Bundle a PDF file as a decoy wrapper

## System Requirements

### For Full Functionality
- Python 3.7+
- PyInstaller (for building executables)
- Required Python packages (see requirements files)

### For GUI Mode
- tkinter (usually included with Python)
- Display server (X11 on Linux, native on Windows/macOS)

### For CLI Mode
- Any terminal environment

## Performance Improvements

The client streaming has been optimized for better quality and speed:

- **Screen Sharing**: JPEG quality increased to 75% (from 35%), optimized encoding
- **Camera**: Resolution set to 1280x720, JPEG quality 80%, optimized encoding
- **Audio**: Maintained high quality with efficient buffering
- **Frame Rate**: Maintained at ~12.5 FPS for smooth performance

## Building Standalone Agents

The build process:
1. Injects server configuration into client.py
2. Uses PyInstaller to create a single executable
3. Optionally bundles custom icon and PDF decoy
4. Adds Windows Defender exclusions for the output

## Security Notes

- Built executables include persistence mechanisms
- Windows Defender exclusions are automatically configured
- Use responsibly and in compliance with applicable laws