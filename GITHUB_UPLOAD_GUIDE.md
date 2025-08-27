# GitHub上传指南

## 概述

本指南将帮助您将可转债量化回测框架项目上传到GitHub，创建名为`Intern_qr_cb`的仓库。

## 步骤1: 在GitHub上创建新仓库

1. **访问您的GitHub账户**: [https://github.com/taopeng111](https://github.com/taopeng111)
2. **点击"New"按钮**: 在页面右上角找到绿色的"New"按钮
3. **填写仓库信息**:
   - **Repository name**: `Intern_qr_cb`
   - **Description**: `可转债量化回测框架 - Convertible Bond Quantitative Backtesting Framework`
   - **Visibility**: 选择 Public（公开）或 Private（私有）
   - **不要勾选**: "Add a README file"、"Add .gitignore"、"Choose a license"
4. **点击"Create repository"**

## 步骤2: 上传项目到GitHub

### 方法1: 使用Python脚本（推荐）

```bash
python upload_to_github.py
```

### 方法2: 使用批处理文件（Windows）

双击运行 `upload_to_github.bat`

### 方法3: 手动执行Git命令

```bash
# 1. 添加所有文件
git add .

# 2. 提交更改
git commit -m "Complete project setup with comprehensive README and all strategies"

# 3. 添加新的远程仓库
git remote add origin_new https://github.com/taopeng111/Intern_qr_cb.git

# 4. 推送到GitHub
git push -u origin_new Ak
```

## 步骤3: 验证上传结果

1. 访问您的GitHub仓库: [https://github.com/taopeng111/Intern_qr_cb](https://github.com/taopeng111/Intern_qr_cb)
2. 确认所有文件都已上传
3. 检查README.md是否正确显示

## 项目结构说明

上传完成后，您的GitHub仓库将包含以下内容：

```
Intern_qr_cb/
├── README.md                           # 项目说明文档
├── constants.py                        # 全局常量和配置
├── CB_Data_Dictionary.txt             # 可转债数据字典
├── data/                              # 数据相关模块
├── framework/                         # 回测框架核心
├── strategies/                        # 投资策略实现
├── run_*.py                          # 回测运行脚本
├── portfolio_backtest.py              # 投资组合回测框架
├── perf_metrics.py                    # 性能指标计算
├── optuna_factor_weights.py           # 因子权重优化
├── upload_to_github.py                # GitHub上传助手
├── upload_to_github.bat               # Windows批处理文件
└── GITHUB_UPLOAD_GUIDE.md            # 本指南
```

## 常见问题解决

### 1. 推送失败 - 仓库不存在
**错误信息**: `fatal: repository 'https://github.com/taopeng111/Intern_qr_cb.git/' not found`

**解决方案**: 确保您已经在GitHub上创建了名为`Intern_qr_cb`的仓库

### 2. 权限问题
**错误信息**: `remote: Permission to taopeng111/Intern_qr_cb.git denied`

**解决方案**: 
- 确保您已登录正确的GitHub账户
- 检查仓库是否为私有仓库，如果是，确保您有访问权限

### 3. 分支问题
**错误信息**: `error: src refspec Ak does not match any`

**解决方案**: 当前项目使用`Ak`分支，如果推送失败，可以尝试：
```bash
git push -u origin_new main
# 或者
git push -u origin_new master
```

## 后续操作

### 1. 设置默认分支
在GitHub仓库设置中，将`Ak`分支设置为默认分支

### 2. 添加仓库描述
在仓库主页添加更详细的描述和标签

### 3. 设置仓库主题
添加相关主题标签，如：`quantitative-finance`, `backtesting`, `convertible-bonds`, `python`

### 4. 邀请协作者（可选）
如果需要团队协作，可以在仓库设置中邀请其他开发者

## 联系支持

如果在上传过程中遇到问题，请：
1. 检查本指南的常见问题部分
2. 查看Git错误信息
3. 确保GitHub账户设置正确

---

**注意**: 本指南假设您已经在本地机器上安装了Git，并且已经配置了GitHub的用户名和邮箱。如果还没有配置，请先运行：
```bash
git config --global user.name "您的GitHub用户名"
git config --global user.email "您的邮箱"
```
