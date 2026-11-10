#!/bin/bash
# Quick install script for Haven TUI
# Usage: curl -fsSL https://raw.githubusercontent.com/haven/haven-cli/main/install.sh | bash

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PACKAGE_NAME="haven-cli"
TUI_ENTRY_POINT="haven-tui"

# Helper functions
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_python() {
    print_info "Checking Python installation..."
    
    if command -v python3 &> /dev/null; then
        PYTHON_CMD="python3"
    elif command -v python &> /dev/null; then
        PYTHON_CMD="python"
    else
        print_error "Python is not installed. Please install Python 3.12 or higher."
        exit 1
    fi
    
    # Check Python version
    PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}')
    PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
    PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)
    
    print_info "Found Python $PYTHON_VERSION"
    
    if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 12 ]); then
        print_error "Python 3.12 or higher is required. Found Python $PYTHON_VERSION"
        exit 1
    fi
    
    print_success "Python version check passed"
}

check_pip() {
    print_info "Checking pip installation..."

    if command -v pip3 &> /dev/null; then
        PIP_CMD="pip3"
    elif command -v pip &> /dev/null; then
        PIP_CMD="pip"
    else
        print_error "pip is not installed. Please install pip."
        exit 1
    fi

    print_success "Found pip"
}

# B16: PyPI wheels for the native `vetkd_py` extension stop at Python 3.13.
# On Python 3.14+ there is no prebuilt wheel, so a plain `pip install` fails.
# When installing from a local checkout, build vetkd_py from source with
# maturin instead. This is a no-op on <=3.13 (the wheel is used normally).
check_vetkd_py() {
    local needs_source_build=0
    if [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -ge 14 ]; then
        needs_source_build=1
    elif [ "$PYTHON_MAJOR" -gt 3 ]; then
        needs_source_build=1
    fi

    if [ "$needs_source_build" -ne 1 ]; then
        print_info "Python $PYTHON_VERSION: prebuilt vetkd_py wheel is available (no source build needed)"
        return 0
    fi

    print_warning "Python $PYTHON_VERSION detected: vetkd_py has no PyPI wheel for 3.14+ (B16)."

    # Only a local checkout actually contains the Rust source to build.
    if [ ! -d "vetkd_py" ] || [ ! -f "vetkd_py/pyproject.toml" ]; then
        print_warning "No local vetkd_py/ source found; cannot build from source."
        print_warning "Install a Python 3.13 environment, or clone the repo and re-run this installer."
        return 0
    fi

    if ! command -v maturin &> /dev/null; then
        print_info "Installing maturin (needed to build the vetkd_py native extension)..."
        $PIP_CMD install maturin --break-system-packages || {
            print_error "Failed to install maturin; cannot build vetkd_py for Python $PYTHON_VERSION"
            return 1
        }
    fi

    if ! command -v cargo &> /dev/null; then
        print_error "Rust toolchain (cargo) not found; required to build vetkd_py for Python 3.14+"
        return 1
    fi

    print_info "Building and installing vetkd_py from source with maturin..."
    ( cd vetkd_py && maturin build --release --interpreter "$PYTHON_CMD" ) || {
        print_error "maturin build failed for vetkd_py"
        return 1
    }
    # Install the produced wheel into the active environment.
    local wheel
    wheel=$(ls -t vetkd_py/target/wheels/*.whl 2>/dev/null | head -n1)
    if [ -n "$wheel" ]; then
        $PIP_CMD install --force-reinstall "$wheel" --break-system-packages
        print_success "vetkd_py built and installed from source ($wheel)"
    else
        print_error "maturin produced no wheel; vetkd_py install incomplete"
        return 1
    fi
}

install_package() {
    print_info "Installing Haven CLI..."
    
    # Check if installing from local source or PyPI
    if [ -f "pyproject.toml" ] && grep -q "haven-cli" pyproject.toml 2>/dev/null; then
        print_info "Installing from local source..."
        $PIP_CMD install -e ".[tui]" --ignore-installed typing_extensions --break-system-packages
    else
        print_info "Installing from PyPI..."
        $PIP_CMD install "$PACKAGE_NAME[tui]" --ignore-installed typing_extensions --break-system-packages
    fi
    
    if [ $? -eq 0 ]; then
        print_success "Haven CLI installed successfully"
    else
        print_error "Failed to install Haven CLI"
        exit 1
    fi
}

check_installation() {
    print_info "Verifying installation..."
    
    if command -v haven &> /dev/null; then
        HAVEN_VERSION=$(haven --version 2>/dev/null || echo "unknown")
        print_success "Haven CLI is available: $HAVEN_VERSION"
    else
        print_warning "Haven CLI command not found in PATH"
        print_info "You may need to add Python's user bin directory to your PATH"
    fi
    
    if command -v $TUI_ENTRY_POINT &> /dev/null; then
        TUI_VERSION=$($TUI_ENTRY_POINT --version 2>/dev/null || echo "unknown")
        print_success "Haven TUI is available: $TUI_VERSION"
    else
        print_warning "Haven TUI command not found in PATH"
        print_info "You may need to add Python's user bin directory to your PATH"
    fi
}

print_usage() {
    echo ""
    echo "=========================================="
    echo "  Haven CLI Installation Complete!"
    echo "=========================================="
    echo ""
    echo "Usage:"
    echo "  haven config init        # Initialize configuration"
    echo "  haven upload <file>      # Upload a video file"
    echo "  haven run                # Start the daemon"
    echo "  $TUI_ENTRY_POINT         # Launch the TUI"
    echo ""
    echo "Documentation:"
    echo "  https://github.com/haven/haven-cli#readme"
    echo ""
}

main() {
    echo "=========================================="
    echo "  Installing Haven CLI"
    echo "=========================================="
    echo ""
    
    check_python
    check_pip
    check_vetkd_py
    install_package
    check_installation
    print_usage
}

# Run main function
main
