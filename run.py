import sys
from src.cli import main

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Program dihentikan.")
        sys.exit(0)
