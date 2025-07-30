# AutoDL GPU 自动监控工具

> 🚀 一个智能的 AutoDL 平台 GPU 资源监控和自动购买工具

本项目专为 AutoDL 平台设计，能够自动监控空闲 GPU 资源，并在满足条件时自动购买启动实例。特别适用于需要抢占稀缺 GPU 资源的深度学习任务场景。

## ✨ 核心功能

- 🔍 **智能监控**: 实时监控指定型号的 GPU 可用性
- ⚡ **自动购买**: 发现可用资源时自动下单购买
- 📱 **即时通知**: 支持 Server酱 微信推送通知
- 🎯 **精准配置**: 支持多种 GPU 型号、数量和镜像配置
- 🔐 **安全登录**: 支持手机号密码登录和微信扫码登录
- 🎮 **双模式运行**: 监控模式 vs 监控+购买模式

## 🚀 快速开始

### 环境准备

确保你的系统已安装 Python 3.8+

```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Linux/macOS
source venv/bin/activate
# Windows
venv\Scripts\activate

# 安装依赖包
pip install -r requirements.txt
```

### 快速运行
   ```bash
   python main.py
   ```

> 💡 首次运行如果没有配置 `authorization`，程序会自动引导你完成登录流程

## ⚙️ 配置详解

项目使用 JSON 格式的配置文件，所有参数都在 `config.json` 中定义。以下是完整的配置说明：

### 📋 配置模板

```json
{
  "authorization": "你的授权令牌",
  "mode": "monitor_and_buy",
  "gpu": {
    "model": "RTX 4090",
    "num": 1
  },
  "image": {
    "private_image_uuid": "",
    "autodl_image": "hub.kce.ksyun.com/autodl-image/torch:cuda12.4-cudnn-devel-ubuntu22.04-py312-torch2.5.1"
  },
  "notification": {
    "sendkey": "SCT123456xxxxxxxxxxxxx"
  }
}
```

### 🔧 参数详解

#### 🔑 authorization
- **功能**: AutoDL 平台授权令牌，用于 API 认证
- **类型**: `string`
- **获取**: 首次运行时程序会自动引导登录获取
- **说明**: 留空时会触发自动登录流程

#### 🎮 mode
- **功能**: 程序运行模式
- **类型**: `string`
- **选项**: 
  - `"monitor_only"` - 👀 仅监控模式（发现资源时通知但不购买）
  - `"monitor_and_buy"` - 💰 监控购买模式（发现资源时自动购买）
- **默认**: `"monitor_only"`
- **推荐**: 测试时使用 `monitor_only`，确认无误后切换到 `monitor_and_buy`

#### 3. gpu 对象
##### 3.1 model
- **说明**: 要监控和购买的GPU型号
- **类型**: 字符串
- **可选值**: 
  - **RTX 系列**: `"RTX 5090"`, `"RTX 4090"`, `"RTX 4090D"`, `"RTX 3090"`, `"RTX 3080*2"`, `"RTX 3080 Ti"`, `"RTX 3080"`, `"RTX 3060"`, `"RTX 2080 Ti *2"`, `"RTX 2080 Ti"`, `"RTX A4000"`
  - **GTX 系列**: `"GTX 1080 Ti"`
  - **专业卡系列**: `"V100-32GB"`, `"V100-SXM2-32GB"`, `"A100-PCIE-40GB"`, `"A100-SXM4-80GB"`, `"A800-80GB"`, `"H800"`, `"H20-NVLink"`, `"L20"`, `"L40"`, `"A40"`, `"Tesla T4"`
  - **虚拟GPU**: `"vGPU-32GB"`, `"vGPU-48GB"`
  - **其他**: `"TITAN Xp"`, `"CPU"`, `"CPU-close-HT"`, `"910B2x鲲鹏920"`
- **示例**: `"TITAN Xp"`

##### num - GPU 数量
- **功能**: 指定购买的 GPU 卡数
- **类型**: `integer`
- **范围**: 1-8（根据 GPU 型号限制）
- **示例**: `1` (单卡), `4` (四卡并行)

#### 🐳 镜像配置

##### private_image_uuid - 私有镜像
- **功能**: 使用自定义私有镜像
- **类型**: `string`
- **获取**: AutoDL 控制台 → 镜像管理 → 查看镜像 UUID
- **示例**: `"image-4933eddca6"`
- **说明**: 优先使用私有镜像，为空则使用公共镜像

##### autodl_image - 公共镜像
- **功能**: 指定 AutoDL 官方镜像
- **类型**: `string`

**🔥 推荐镜像**:
| 框架版本 | 镜像地址 | 适用场景 |
|----------|----------|----------|
| PyTorch 2.5.1 | `hub.kce.ksyun.com/autodl-image/torch:cuda12.4-cudnn-devel-ubuntu22.04-py312-torch2.5.1` | 最新版本 |
| PyTorch 2.3.0 | `hub.kce.ksyun.com/autodl-image/torch:cuda12.1-cudnn-devel-ubuntu22.04-py312-torch2.3.0` | 稳定版本 |
| PyTorch 2.1.2 | `hub.kce.ksyun.com/autodl-image/torch:cuda11.8-cudnn8-devel-ubuntu22.04-py310-torch2.1.2` | 兼容性好 |

**📸 镜像选择指南**:

<details>
<summary>🖼️ 如何选择公共镜像</summary>

![公共镜像选择](assert/auto_image.png)

1. 进入 AutoDL 控制台
2. 选择"创建实例"
3. 在镜像选择中找到对应版本
4. 复制镜像地址到配置文件

</details>

<details>
<summary>🔒 如何使用私有镜像</summary>

![私有镜像使用](assert/auto_private_image.png)

1. 进入 AutoDL 控制台
2. 点击"我的镜像"
3. 找到目标镜像的 UUID
4. 复制到 `private_image_uuid` 字段

</details>

#### 📱 通知配置

##### sendkey - Server酱推送
- **功能**: 微信消息推送服务
- **类型**: `string`
- **格式**: `SCT123456xxxxxxxxxxxxx`
- **获取方式**: [Server酱官网](https://sct.ftqq.com/) 注册获取
- **说明**: 为空时跳过消息推送

**🔔 通知设置指南**:

<details>
<summary>📱 如何获取 Server酱 SendKey</summary>

![Server酱登录](assert/server_login.png)
![Server酱密钥](assert/server_sendkey.png)
</details>


本项目部分代码参考了 [autogpu](https://github.com/lemonmindyes/autogpu) 项目，感谢原作者的贡献。

---

<div align="center">

