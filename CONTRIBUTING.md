# 贡献指南 · Contributing

从仓库根目录运行。准备 Python 3.9+ 和 Node.js 18+，安装 Pillow。测试使用标准库 `unittest` 与虚构的曼谷样例。

```bash
python3 -m pip install Pillow
python3 -m unittest discover -s tests
```

修改版本时，更新 `want-to-go-trip-planner/config/product.json` 中的 `version` 和 `passportTemplateMarker`，并更新 [CHANGELOG.md](CHANGELOG.md)。只有真正变更相应契约时才更新契约版本。若工作流中的包名包含版本，也一并更新。

```bash
python3 want-to-go-trip-planner/scripts/build_release.py --zip dist/want-to-go-trip-planner-skill-3.2.2.zip
python3 want-to-go-trip-planner/scripts/build_release.py --verify --zip dist/want-to-go-trip-planner-skill-3.2.2.zip
python3 want-to-go-trip-planner/scripts/want_to_go.py scan --mode package --path dist/want-to-go-trip-planner-skill-3.2.2.zip
```

构建会重写 Skill 内的 `release-manifest.json`，并生成 ZIP 与 SHA-256 文件。提交清单，ZIP 与校验文件作为 Release 附件。提交前用 [曼谷样例](examples/bangkok/README.md) 重放护照，不提交真实收藏、截图、凭证链接或本机路径。安全问题请走 [私密报告](SECURITY.md)。

Run the same commands at the repository root. Update the product version, passport marker and changelog together. Commit the regenerated Skill manifest and upload the ZIP and checksum as release assets. Use synthetic fixtures for public reports.
