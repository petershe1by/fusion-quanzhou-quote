# Fusion 铨洲 6.8 活动报价

在 Fusion 的设计工作区打开 **铨洲 6.8 活动报价**，确认零件后点 **打开活动报价**。插件导出一个独立的 STEP，打开铨洲智造的 **6.8 起打样**。浏览器扩展把 STEP 交给活动页的上传控件，并按 Fusion 对话框中的设置确认上传报价。

## 安装一次

1. 先解压 ZIP 到准备长期保留的位置。里面有 `FusionQuanzhouQuote` 和 `BrowserExtension` 两个文件夹。
2. 打开 Windows 版 Autodesk Fusion，按 **Shift+S**，切到 **附加模块（Add-Ins）**。点击 **+**，按界面提示选择现有附加模块并定位到 `FusionQuanzhouQuote` 文件夹。也可以把此文件夹复制进 `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns`，再重启 Fusion。选中 `FusionQuanzhouQuote` 后点 **运行**。需要每次启动都可用时，启用 **启动时运行**。 [Autodesk 安装说明](https://help.autodesk.com/view/fusion360/ENU/?caas=caas%2Fsfdcarticles%2Fsfdcarticles%2FHow-to-install-an-ADD-IN-and-Script-in-Fusion-360.html)
3. 在系统默认浏览器的常用配置文件中加载 `BrowserExtension`。Edge 打开 `edge://extensions`，Chrome 打开 `chrome://extensions`。开启 **开发人员模式**，选择 **加载解压缩的扩展**，定位到包含 `manifest.json` 的 `BrowserExtension` 文件夹。 [Microsoft Edge 加载说明](https://learn.microsoft.com/en-us/microsoft-edge/extensions/getting-started/extension-sideloading)

升级时，用新版 `FusionQuanzhouQuote` 文件夹中的文件覆盖原附加模块，再重启 Fusion。已加载的浏览器扩展可以继续使用。

## 使用

- **独立零件文档**可以从 **设计 → 实用程序 → 铨洲 6.8 活动报价** 打开。文档只有一个实体时，插件会自动选中它。文档有多个实体时，在设计树的 **实体** 文件夹内选出需要报价的一个。
- **装配体**中先在设计树选中一个零件组件，再右键打开 **铨洲 6.8 活动报价**。也可以点中零件上的一个面，并使用同一个报价命令。选中子装配时，展开后点选里面的零件。零件组件含多个实体时，展开 **实体** 文件夹选一个实体。
- **更改 STEP 保存位置**时，在报价窗口打开 **更改位置 → 选择文件夹…**。选定后即保存，之后的报价和重启 Fusion 都会使用该位置。每次导出在所选目录下建立带日期和时间的子文件夹，避免同名 STEP 相互覆盖。也可选 **恢复默认位置**，切回 `%LOCALAPPDATA%\FusionQuanzhouQuote\exports`。
- 首次使用时，如果活动页提示 **请先登录**，在官网正常登录即可。扩展会等上传控件启用后自动交接。图纸按零件自身的坐标系导出，请在官网核对加工方向和仿真。
- Fusion 默认勾选 **自动确认 6061 报价**。扩展会核对确认框里只有刚导出的一个 STEP，且材料为铝合金 6061，再点 **确认上传**。想选其他材料或先检查工艺，取消勾选后在官网手动确认。6.8 元是起价，实际价格看解析结果。扩展的操作结束于提交报价用的图纸，后续下单由用户在官网完成。 [铨洲 6.8 起打样](https://www.gy2025.com/#/home/proofing)
- 如果浏览器扩展缺失、网页改版或交接超时，可以用本地页面的 **复制文件路径** 和 **打开 6.8 起打样**。自动交接在 Fusion 打开期间有效，最长 30 分钟。活动页的 STEP 文件上限为 7 MiB。报价窗口中的 **STEP 保存位置**会显示当前目录。

## 数据流

Fusion 直接导出只含一个实体的独立零件。需要从多实体组件中单独取一件时，插件在命令结束后将所选实体复制到临时设计导出，随后关闭临时设计。浏览器扩展通过 `127.0.0.1` 上的临时链接领取这一个文件。官网登录使用你平时的浏览器会话，扩展使用官网自己的 STEP 上传控件。窗口中的文件交接提示仅报告浏览器这一步。

参考了野生齿轮的 SolidWorks 宏和附件中的选件、导出及确认上传流程。Fusion 版使用 Autodesk Fusion API 和浏览器扩展实现同一流程，操作时可以继续使用鼠标和键盘。 [RoboMaster 原帖](https://bbs.robomaster.com/article/1982836?source=4)
