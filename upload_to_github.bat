@echo off
chcp 65001 >nul
echo 🚀 可转债量化回测框架 - GitHub上传助手
echo ==================================================

echo.
echo 📋 请先在GitHub上创建仓库 'Intern_qr_cb'
echo 1. 访问: https://github.com/taopeng111
echo 2. 点击 'New' 或 '+' 按钮
echo 3. 仓库名: Intern_qr_cb
echo 4. 描述: 可转债量化回测框架
echo 5. 选择 Public 或 Private
echo 6. 不要勾选 'Initialize with README'
echo 7. 点击 'Create repository'
echo.

pause

echo 🔄 检查Git状态...
git status
if %errorlevel% neq 0 (
    echo ❌ Git状态检查失败，请确保在Git仓库中运行此脚本
    pause
    exit /b 1
)

echo.
echo 🔄 添加所有文件到暂存区...
git add .
if %errorlevel% neq 0 (
    echo ❌ 添加文件失败
    pause
    exit /b 1
)

echo.
echo 🔄 提交更改...
git commit -m "Complete project setup with comprehensive README and all strategies"
if %errorlevel% neq 0 (
    echo ❌ 提交更改失败
    pause
    exit /b 1
)

echo.
echo 🔄 添加新的远程仓库...
git remote add origin_new https://github.com/taopeng111/Intern_qr_cb.git
if %errorlevel% neq 0 (
    echo ❌ 添加远程仓库失败
    pause
    exit /b 1
)

echo.
echo 🔄 推送到GitHub...
git push -u origin_new Ak
if %errorlevel% neq 0 (
    echo.
    echo ⚠️  推送失败！可能的原因：
    echo 1. GitHub仓库 'Intern_qr_cb' 还没有创建
    echo 2. 需要先在GitHub上创建仓库
    echo.
    echo 请按照上述步骤创建仓库后，重新运行此脚本
    pause
    exit /b 1
)

echo.
echo 🎉 项目成功上传到GitHub!
echo 📁 仓库地址: https://github.com/taopeng111/Intern_qr_cb
echo 🔗 您可以在GitHub上查看您的项目了
echo.

pause
