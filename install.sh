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
    install_package
    check_installation
    print_usage
}

# Run main function
main
