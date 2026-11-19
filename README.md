# Fusion 铨洲 6.8 活动报价

在 Fusion 中选中一个或多个零件，点 **铨洲一键报价**。插件批量导出 STEP，浏览器扩展把这一批传给铨洲智造的 **6.8 起打样**并以官网默认的铝合金 6061 确认上传。已有铨洲标签页时，复用其中的活动页或用现有铨洲标签页打开活动入口。 [铨洲 6.8 起打样](https://www.gy2025.com/#/home/proofing)

## 安装一次

1. 先解压 ZIP 到准备长期保留的位置。里面有 `FusionQuanzhouQuote` 和 `BrowserExtension` 两个文件夹。
2. 打开 Windows 版 Autodesk Fusion，按 **Shift+S**，切到 **附加模块（Add-Ins）**。点击 **+**，按界面提示选择现有附加模块并定位到 `FusionQuanzhouQuote` 文件夹。也可以把此文件夹复制进 `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns`，再重启 Fusion。选中 `FusionQuanzhouQuote` 后点 **运行**。需要每次启动都可用时，启用 **启动时运行**。 [Autodesk 安装说明](https://help.autodesk.com/view/fusion360/ENU/?caas=caas%2Fsfdcarticles%2Fsfdcarticles%2FHow-to-install-an-ADD-IN-and-Script-in-Fusion-360.html)
3. 在平时用来登录铨洲的 Edge 或 Chrome 配置文件中加载 `BrowserExtension`。Edge 打开 `edge://extensions`，Chrome 打开 `chrome://extensions`。开启 **开发人员模式**，选择 **加载解压缩的扩展**，定位到包含 `manifest.json` 的 `BrowserExtension` 文件夹。建议只在日常报价的浏览器里启用该扩展。 [Microsoft Edge 加载说明](https://learn.microsoft.com/en-us/microsoft-edge/extensions/getting-started/extension-sideloading)

从 1.0.x 升级时，用新版 `FusionQuanzhouQuote` 覆盖原附加模块，再重启 Fusion。在扩展管理页移除旧的「Fusion 铨洲 6.8 活动报价」，再加载新版 `BrowserExtension`。1.1.0 使用固定的扩展 ID 与 Fusion 配对，扩展名下的版本应显示为 **1.1.0**。

## 使用

- **装配体**中按 Ctrl 从设计树多选零件组件，也可以选实体或实体面。右键选 **铨洲一键报价**，或在 **设计 → 实用程序** 里点同名按钮。子装配需展开后再选零件，多实体组件需展开 **实体** 文件夹后选具体实体。
- **独立零件文档**只有一个实体时，可直接点 **铨洲一键报价**。文档有多个实体时，先多选要报价的实体。
- **STEP 保存位置**在原 **铨洲 6.8 活动报价** 窗口里修改。每批文件集中保存在当前目录下的一个日期时间子文件夹。同名零件的文件会加编号。同一实体选中多个面或多个相同实例时只导出一份，零件数量可以在官网调整。
- 一批最多 100 个 STEP，每个文件上限 7 MiB。扩展会在网页确认框内核对这一批文件及 6061 材质，再点 **确认上传**。切换材质时可通过原 **铨洲 6.8 活动报价** 窗口关闭自动确认。实际价格、加工仿真和下单仍在官网查看和操作。
- 官网尚未登录时，扩展会等待登录。页内已有待确认的上传时，它会等待该确认框关闭。首次唤醒浏览器扩展可能需要约半分钟。确实没有铨洲标签页时，扩展会开一个；之后的批次继续复用它。

## 数据流

Fusion 直接导出单实体组件。需要从多实体组件中取出一个实体时，插件在命令结束后将它复制到临时设计导出，随后关闭临时设计。扩展从 `127.0.0.1:49167` 领取主动提交的这批文件，使用当前浏览器的登录状态和官网自己的上传控件。Fusion 和浏览器扩展都需更新为 1.1.0，已选保存目录会保留。

参考了野生齿轮的 SolidWorks 宏和附件中的选件、导出及确认上传流程。Fusion 版使用 Autodesk Fusion API 和浏览器扩展实现同一流程，操作时可以继续使用鼠标和键盘。 [RoboMaster 原帖](https://bbs.robomaster.com/article/1982836?source=4)
