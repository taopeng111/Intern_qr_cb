#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub上传助手脚本
帮助将可转债量化回测框架上传到GitHub仓库
"""

import os
import subprocess
import sys
from pathlib import Path

def run_command(command, description):
    """运行命令并处理结果"""
    print(f"🔄 {description}...")
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(f"✅ {description} 成功")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} 失败: {e}")
        print(f"错误输出: {e.stderr}")
        return False

def main():
    """主函数"""
    print("🚀 可转债量化回测框架 - GitHub上传助手")
    print("=" * 50)
    
    # 检查Git状态
    if not run_command("git status", "检查Git状态"):
        print("❌ Git状态检查失败，请确保在Git仓库中运行此脚本")
        return
    
    # 添加所有文件
    if not run_command("git add .", "添加所有文件到暂存区"):
        return
    
    # 提交更改
    commit_message = "Complete project setup with comprehensive README and all strategies"
    if not run_command(f'git commit -m "{commit_message}"', "提交更改"):
        return
    
    # 添加新的远程仓库
    new_remote = "https://github.com/taopeng111/Intern_qr_cb.git"
    if not run_command(f'git remote add origin_new {new_remote}', "添加新的远程仓库"):
        return
    
    # 推送到新仓库
    if not run_command("git push -u origin_new Ak", "推送到GitHub"):
        print("\n⚠️  推送失败！可能的原因：")
        print("1. GitHub仓库 'Intern_qr_cb' 还没有创建")
        print("2. 需要先在GitHub上创建仓库")
        print("\n📋 创建仓库步骤：")
        print("1. 访问: https://github.com/taopeng111")
        print("2. 点击 'New' 或 '+' 按钮")
        print("3. 仓库名: Intern_qr_cb")
        print("4. 描述: 可转债量化回测框架")
        print("5. 选择 Public 或 Private")
        print("6. 不要勾选 'Initialize with README'")
        print("7. 点击 'Create repository'")
        print("\n创建完成后，重新运行此脚本即可")
        return
    
    print("\n🎉 项目成功上传到GitHub!")
    print(f"📁 仓库地址: {new_remote}")
    print("🔗 您可以在GitHub上查看您的项目了")

if __name__ == "__main__":
    main()
