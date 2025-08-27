#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Upload Assistant Script
Help upload the convertible bond quantitative backtesting framework to GitHub repository
"""

import os
import subprocess
import sys
from pathlib import Path

def run_command(command, description):
    """Run command and handle results"""
    print(f"🔄 {description}...")
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(f"✅ {description} successful")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} failed: {e}")
        print(f"Error output: {e.stderr}")
        return False

def main():
    """Main function"""
    print("🚀 Convertible Bond Quantitative Backtesting Framework - GitHub Upload Assistant")
    print("=" * 50)
    
    # Check Git status
    if not run_command("git status", "Check Git status"):
        print("❌ Git status check failed, please ensure running this script in a Git repository")
        return
    
    # Add all files
    if not run_command("git add .", "Add all files to staging area"):
        return
    
    # Commit changes
    commit_message = "Complete project setup with comprehensive README and all strategies"
    if not run_command(f'git commit -m "{commit_message}"', "Commit changes"):
        return
    
    # Add new remote repository
    new_remote = "https://github.com/taopeng111/Intern_qr_cb.git"
    if not run_command(f'git remote add origin_new {new_remote}', "Add new remote repository"):
        return
    
    # Push to new repository
    if not run_command("git push -u origin_new Ak", "Push to GitHub"):
        print("\n⚠️  Push failed! Possible reasons:")
        print("1. GitHub repository 'Intern_qr_cb' hasn't been created yet")
        print("2. Need to create repository on GitHub first")
        print("\n📋 Repository creation steps:")
        print("1. Visit: https://github.com/taopeng111")
        print("2. Click 'New' or '+' button")
        print("3. Repository name: Intern_qr_cb")
        print("4. Description: Convertible Bond Quantitative Backtesting Framework")
        print("5. Choose Public or Private")
        print("6. Do NOT check 'Initialize with README'")
        print("7. Click 'Create repository'")
        print("\nAfter creation, run this script again")
        return
    
    print("\n🎉 Project successfully uploaded to GitHub!")
    print(f"📁 Repository address: {new_remote}")
    print("🔗 You can now view your project on GitHub")

if __name__ == "__main__":
    main()
