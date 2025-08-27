@echo off
chcp 65001 >nul
echo 🚀 Convertible Bond Quantitative Backtesting Framework - GitHub Upload Assistant
echo ==================================================

echo.
echo 📋 Please create repository 'Intern_qr_cb' on GitHub first
echo 1. Visit: https://github.com/taopeng111
echo 2. Click 'New' or '+' button
echo 3. Repository name: Intern_qr_cb
echo 4. Description: Convertible Bond Quantitative Backtesting Framework
echo 5. Choose Public or Private
echo 6. Do NOT check 'Initialize with README'
echo 7. Click 'Create repository'
echo.

pause

echo 🔄 Checking Git status...
git status
if %errorlevel% neq 0 (
    echo ❌ Git status check failed, please ensure running this script in a Git repository
    pause
    exit /b 1
)

echo.
echo 🔄 Adding all files to staging area...
git add .
if %errorlevel% neq 0 (
    echo ❌ Adding files failed
    pause
    exit /b 1
)

echo.
echo 🔄 Committing changes...
git commit -m "Complete project setup with comprehensive README and all strategies"
if %errorlevel% neq 0 (
    echo ❌ Committing changes failed
    pause
    exit /b 1
)

echo.
echo 🔄 Adding new remote repository...
git remote add origin_new https://github.com/taopeng111/Intern_qr_cb.git
if %errorlevel% neq 0 (
    echo ❌ Adding remote repository failed
    pause
    exit /b 1
)

echo.
echo 🔄 Pushing to GitHub...
git push -u origin_new Ak
if %errorlevel% neq 0 (
    echo.
    echo ⚠️  Push failed! Possible reasons:
    echo 1. GitHub repository 'Intern_qr_cb' hasn't been created yet
    echo 2. Need to create repository on GitHub first
    echo.
    echo Please follow the above steps to create repository, then run this script again
    pause
    exit /b 1
)

echo.
echo 🎉 Project successfully uploaded to GitHub!
echo 📁 Repository address: https://github.com/taopeng111/Intern_qr_cb
echo 🔗 You can now view your project on GitHub
echo.

pause
