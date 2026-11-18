# 参与贡献

感谢你愿意改进 NEUROLINK。这个项目刻意保持「单文件 + 零第三方依赖」，改动请尽量贴合这个风格。

## 报 Bug

请附上这些信息，能省掉大半来回：

- Windows 版本（`winver` 截图或版本号）
- 是否以管理员运行（界面右上角的徽标是 ADMIN 还是普通权限）
- 录制在哪个程序里、回放在哪个程序里
- `运行日志.txt` 里对应的行（不要整份贴，截相关部分）

## 提 PR

- 先开 Issue 说明要改什么，避免大改动做完了发现方向不一致。
- Python 侧只使用标准库 + `pynput`；新增依赖请在 PR 里说明必要性。
- 保持 PEP 8，注释写「为什么」，不写「做了什么」。
- 界面改动请附改动前后的截图（放在 PR 描述里即可，不要进仓库）。
- `scripts/*.json` 是你的本地录制产物，除 `scripts/示例-矩形轨迹.json` 外不要提交。

## 开发环境

```bash
git clone https://github.com/Susky0-o/neurolink-clicker.git
cd neurolink-clicker
python app.py --browser   # 调试界面时用浏览器模式，改完刷新即可
```

改完 `launcher.cs` 记得跑一次 `build_exe.ps1` 验证还能编译出能用的 exe。
