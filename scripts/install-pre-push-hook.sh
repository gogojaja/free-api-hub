#!/bin/bash
# 安装 pre-push hook，阻止密钥文件上传到 GitHub

HOOK=".git/hooks/pre-push"

cat > "$HOOK" << 'HOOKEOF'
#!/bin/bash
# 禁止包含 API Key 的配置文件被推送

BLOCKED=("config/chat.yaml" "config/code.yaml")

while read local_ref local_oid remote_ref remote_oid; do
  for file in "${BLOCKED[@]}"; do
    if git diff --cached --name-only | grep -q "$file" ||
       git diff "$remote_oid..$local_oid" --name-only 2>/dev/null | grep -q "$file"; then
      echo "⛔ 禁止推送 $file（包含 API Key）"
      echo "   如需推送请移除 pre-push hook：rm .git/hooks/pre-push"
      exit 1
    fi
  done
done
exit 0
HOOKEOF

chmod +x "$HOOK"
echo "✅ pre-push hook 已安装: $HOOK"
