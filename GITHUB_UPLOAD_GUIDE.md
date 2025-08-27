# GitHub Upload Guide

## Overview

This guide will help you upload the convertible bond quantitative backtesting framework project to GitHub, creating a repository named `Intern_qr_cb`.

## Step 1: Create New Repository on GitHub

1. **Visit your GitHub account**: [https://github.com/taopeng111](https://github.com/taopeng111)
2. **Click "New" button**: Find the green "New" button in the top right corner
3. **Fill in repository information**:
   - **Repository name**: `Intern_qr_cb`
   - **Description**: `Convertible Bond Quantitative Backtesting Framework - Convertible Bond Quantitative Backtesting Framework`
   - **Visibility**: Choose Public or Private
   - **Do NOT check**: "Add a README file", "Add .gitignore", "Choose a license"
4. **Click "Create repository"**

## Step 2: Upload Project to GitHub

### Method 1: Using Python Script (Recommended)

```bash
python upload_to_github.py
```

### Method 2: Using Batch File (Windows)

Double-click to run `upload_to_github.bat`

### Method 3: Manual Git Commands

```bash
# 1. Add all files
git add .

# 2. Commit changes
git commit -m "Complete project setup with comprehensive README and all strategies"

# 3. Add new remote repository
git remote add origin_new https://github.com/taopeng111/Intern_qr_cb.git

# 4. Push to GitHub
git push -u origin_new Ak
```

## Step 3: Verify Upload Results

1. Visit your GitHub repository: [https://github.com/taopeng111/Intern_qr_cb](https://github.com/taopeng111/Intern_qr_cb)
2. Confirm all files have been uploaded
3. Check if README.md displays correctly

## Project Structure Description

After upload completion, your GitHub repository will contain:

```
Intern_qr_cb/
├── README.md                           # Project documentation
├── constants.py                        # Global constants and configuration
├── CB_Data_Dictionary.txt             # Convertible bond data dictionary
├── data/                              # Data-related modules
├── framework/                         # Backtesting framework core
├── strategies/                        # Investment strategy implementations
├── run_*.py                          # Backtesting run scripts
├── portfolio_backtest.py              # Portfolio backtesting framework
├── perf_metrics.py                    # Performance metrics calculation
├── optuna_factor_weights.py           # Factor weight optimization
├── upload_to_github.py                # GitHub upload assistant
├── upload_to_github.bat               # Windows batch file
└── GITHUB_UPLOAD_GUIDE.md            # This guide
```

## Common Problem Solutions

### 1. Push Failed - Repository Doesn't Exist
**Error message**: `fatal: repository 'https://github.com/taopeng111/Intern_qr_cb.git/' not found`

**Solution**: Ensure you have created a repository named `Intern_qr_cb` on GitHub

### 2. Permission Issues
**Error message**: `remote: Permission to taopeng111/Intern_qr_cb.git denied`

**Solution**: 
- Ensure you are logged into the correct GitHub account
- Check if the repository is private, if so, ensure you have access permissions

### 3. Branch Issues
**Error message**: `error: src refspec Ak does not match any`

**Solution**: The current project uses the `Ak` branch. If push fails, try:
```bash
git push -u origin_new main
# or
git push -u origin_new master
```

## Follow-up Operations

### 1. Set Default Branch
In GitHub repository settings, set the `Ak` branch as the default branch

### 2. Add Repository Description
Add more detailed descriptions and tags on the repository homepage

### 3. Set Repository Topics
Add relevant topic tags such as: `quantitative-finance`, `backtesting`, `convertible-bonds`, `python`

### 4. Invite Collaborators (Optional)
If team collaboration is needed, you can invite other developers in repository settings

## Contact Support

If you encounter problems during upload, please:
1. Check the common problems section of this guide
2. Review Git error messages
3. Ensure GitHub account settings are correct

---

**Note**: This guide assumes you have Git installed on your local machine and have configured your GitHub username and email. If not yet configured, please run first:
```bash
git config --global user.name "Your GitHub Username"
git config --global user.email "Your Email"
```
