#!/usr/bin/env python3
"""
Script to update Python dependencies for Pitivi Flatpak build.

This script handles cloning/updating flatpak-builder-tools and generating
the Python module JSON files needed for the Flatpak build.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List


class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    NC = '\033[0m'  # No Color


def log_info(message: str) -> None:
    print(f"{Colors.GREEN}[INFO]{Colors.NC} {message}")


def log_warn(message: str) -> None:
    print(f"{Colors.YELLOW}[WARN]{Colors.NC} {message}")


def log_error(message: str) -> None:
    print(f"{Colors.RED}[ERROR]{Colors.NC} {message}")


def check_uv() -> None:
    """Check if uv is installed and available."""
    try:
        result = subprocess.run(['uv', '--version'], capture_output=True, text=True, check=True)
        log_info(f"Found uv: {result.stdout.strip()}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        log_error("uv is not installed. Please install it first:")
        print("  curl -LsSf https://astral.sh/uv/install.sh | sh")
        print("  or visit: https://docs.astral.sh/uv/getting-started/installation/")
        sys.exit(1)


def setup_tools(tools_dir: Path) -> None:
    """Clone or update flatpak-builder-tools repository."""
    if not tools_dir.exists():
        log_info("Cloning flatpak-builder-tools...")
        subprocess.run([
            'git', 'clone', 
            'https://github.com/flatpak/flatpak-builder-tools.git', 
            str(tools_dir)
        ], check=True)
    else:
        log_info("Updating flatpak-builder-tools...")
        subprocess.run(['git', 'pull', 'origin', 'master'], 
                      cwd=tools_dir, check=True)


def get_wheel_urls_for_arches(package_name: str, version: str) -> List[dict]:
    """Get wheel URLs for both x86_64 and aarch64 architectures."""
    import urllib.request
    import json
    import hashlib
    
    wheels = []
    
    try:
        # Get package info from PyPI
        with urllib.request.urlopen(f"https://pypi.org/pypi/{package_name}/json") as response:
            data = json.loads(response.read())
        
        if version not in data['releases']:
            log_warn(f"Version {version} not found for {package_name}")
            return wheels
        
        # Look for cp312 wheels for both architectures
        for file_info in data['releases'][version]:
            filename = file_info['filename']
            
            # Check for Python 3.12 wheels for our target architectures
            if 'cp312' in filename and filename.endswith('.whl'):
                if 'manylinux' in filename or 'linux' in filename:
                    arch = None
                    if 'x86_64' in filename:
                        arch = 'x86_64'
                    elif 'aarch64' in filename:
                        arch = 'aarch64'
                    
                    if arch:
                        wheels.append({
                            'type': 'file',
                            'url': file_info['url'],
                            'sha256': file_info['digests']['sha256'],
                            'only-arches': [arch]
                        })
        
        if wheels:
            log_info(f"Found wheels for {package_name} {version}: {len(wheels)} architecture(s)")
        
    except Exception as e:
        log_warn(f"Could not fetch wheel info for {package_name}: {e}")
    
    return wheels


def generate_consolidated_module(output_file: Path, packages_with_args: List[tuple], tools_dir: Path) -> None:
    """Generate consolidated Python module JSON using flatpak-pip-generator."""
    import json
    import tempfile
    
    log_info(f"Generating consolidated {output_file.name}")
    
    generator_script = tools_dir / 'pip' / 'flatpak-pip-generator'
    
    # Generate temporary JSON for all packages
    all_packages = []
    for packages, extra_args in packages_with_args:
        all_packages.extend(packages)
    
    log_info(f"Generating dependencies for packages: {', '.join(all_packages)}")
    
    try:
        # Generate the base JSON with all packages
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as temp_file:
            temp_path = Path(temp_file.name)
        
        subprocess.run([
            'uv', 'run', str(generator_script), *all_packages, 
            '-o', str(temp_path)
        ], check=True)
        
        # Load the generated JSON
        with open(temp_path, 'r') as f:
            module_data = json.load(f)
        
        # Extract all sources from nested modules into a single sources list
        all_sources = []
        if 'modules' in module_data:
            for module in module_data['modules']:
                if 'sources' in module:
                    all_sources.extend(module['sources'])
        
        # Special handling for librosa dependencies that need wheels
        # Replace source distributions with wheels for numba and llvmlite
        filtered_sources = []
        added_numba_wheels = False
        added_llvmlite_wheels = False
        seen_urls = set()  # Track URLs to avoid duplicates
        
        for source in all_sources:
            if source.get('type') == 'file' and 'url' in source:
                url = source['url']
                
                # Skip duplicates
                if url in seen_urls:
                    continue
                    
                # Check if this is numba or llvmlite source distribution
                if 'numba-0' in url and url.endswith('.tar.gz'):
                    if not added_numba_wheels:
                        # Replace with wheels for both architectures
                        log_info("Replacing numba source with wheels for both architectures")
                        wheels = get_wheel_urls_for_arches('numba', '0.60.0')
                        if wheels:
                            filtered_sources.extend(wheels)
                            added_numba_wheels = True
                        else:
                            # Fallback to source if no wheels found
                            filtered_sources.append(source)
                            seen_urls.add(url)
                elif 'llvmlite-0' in url and url.endswith('.tar.gz'):
                    if not added_llvmlite_wheels:
                        # Replace with wheels for both architectures
                        log_info("Replacing llvmlite source with wheels for both architectures")
                        wheels = get_wheel_urls_for_arches('llvmlite', '0.43.0')
                        if wheels:
                            filtered_sources.extend(wheels)
                            added_llvmlite_wheels = True
                        else:
                            # Fallback to source if no wheels found
                            filtered_sources.append(source)
                            seen_urls.add(url)
                else:
                    # Keep all other sources as-is (but no duplicates)
                    filtered_sources.append(source)
                    seen_urls.add(url)
        
        # Rebuild build-commands with proper grouping and extra args
        new_build_commands = []
        
        for packages, extra_args in packages_with_args:
            for package in packages:
                base_cmd = f'pip3 install --verbose --exists-action=i --no-index --find-links="file://${{PWD}}" --prefix=${{FLATPAK_DEST}} "{package}" --no-build-isolation'
                if extra_args:
                    base_cmd += f' {extra_args}'
                new_build_commands.append(base_cmd)
        
        # Create the consolidated module structure
        module_data = {
            'name': 'python3-modules',
            'buildsystem': 'simple',
            'build-commands': new_build_commands,
            'sources': filtered_sources
        }
        
        # Write the final consolidated JSON
        with open(output_file, 'w') as f:
            json.dump(module_data, f, indent=4)
            
        # Clean up temp file
        temp_path.unlink()
        
        log_info(f"Generated consolidated {output_file.name} successfully")
        
    except subprocess.CalledProcessError as e:
        log_error(f"Failed to generate {output_file.name}: {e}")
        if e.stderr:
            print(f"Error output: {e.stderr}")
        sys.exit(1)


def update_all_deps(script_dir: Path, tools_dir: Path) -> None:
    """Update all Python dependencies."""
    log_info("Starting consolidated Python dependencies update...")
    
    # Define packages with their extra pip arguments
    # Format: (packages_list, extra_args)
    packages_with_args = [
        (['setuptools_scm'], ''),
        (['numpy'], ''),
        (['pythran'], ''),
        (['pybind11'], ''),
        (['cppy'], ''),
        (['matplotlib'], "--config-settings=setup-args='-Dsystem-freetype=true' --config-settings=setup-args='-Dsystem-qhull=true'"),
        (['scipy'], ''),
        (['librosa'], ''),
    ]
    
    generate_consolidated_module(script_dir / 'python3-modules.json', packages_with_args, tools_dir)
    
    log_info("Consolidated Python dependencies updated successfully!")
    log_warn("Don't forget to test the build with: flatpak-builder build-dir org.pitivi.Pitivi.json")


def main():
    parser = argparse.ArgumentParser(
        description="Update Python dependencies for Pitivi Flatpak build",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                    # Update all packages in consolidated module
  %(prog)s --packages matplotlib librosa  # Update only specific packages
  %(prog)s --setup-only       # Only clone/update tools
        """
    )
    
    parser.add_argument(
        '--packages', 
        nargs='+',
        help='Update only specific packages (e.g., --packages matplotlib librosa)'
    )
    
    parser.add_argument(
        '--setup-only', 
        action='store_true',
        help='Only setup flatpak-builder-tools'
    )
    
    args = parser.parse_args()
    
    script_dir = Path(__file__).parent.absolute()
    tools_dir = script_dir / 'flatpak-builder-tools'
    
    # Always check for uv first
    check_uv()
    
    # Setup tools
    setup_tools(tools_dir)
    
    if args.setup_only:
        log_info(f"Setup complete. Tools available at: {tools_dir}")
        return
    
    # Package-specific configuration
    all_packages_config = {
        'setuptools_scm': '',
        'numpy': '',
        'pythran': '',
        'pybind11': '',
        'cppy': '',
        'matplotlib': "--config-settings=setup-args='-Dsystem-freetype=true' --config-settings=setup-args='-Dsystem-qhull=true'",
        'scipy': '',
        'librosa': '',
    }
    
    if args.packages:
        # Generate consolidated module with only specified packages
        packages_with_args = []
        for package in args.packages:
            if package in all_packages_config:
                packages_with_args.append(([package], all_packages_config[package]))
            else:
                log_warn(f"Unknown package: {package}. Adding without special configuration.")
                packages_with_args.append(([package], ''))
        
        generate_consolidated_module(script_dir / 'python3-modules.json', packages_with_args, tools_dir)
    else:
        # Update all dependencies
        update_all_deps(script_dir, tools_dir)


if __name__ == '__main__':
    main()