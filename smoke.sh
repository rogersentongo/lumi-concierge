#!/usr/bin/env bash
# Lumi smoke test — exercises the full stack (Mac app -> box -> ElevenLabs + media).
#   ./smoke.sh
set -u
cd "$(dirname "$0")"
APP=${APP:-http://localhost:8765}
TOKEN=$(grep '^INFERENCE_AUTH_TOKEN=' .env | cut -d= -f2)
BOX=$(grep '^EMOTION_SERVER_URL=' .env | cut -d= -f2)
FAILED=0
pass(){ echo "  ✓ $1"; }
fail(){ echo "  ✗ $1"; FAILED=1; }

echo "== health =="
curl -s "$APP/healthz" | grep -q '"ok":true' && pass "mac app up" || fail "mac app down"
curl -s "$BOX/healthz" | grep -q '"ok":true' && pass "box server up" || fail "box server down"

echo "== box /answer grounded + media (eager breakfast) =="
RESP=$(curl -s -X POST "$BOX/answer" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"what is good for breakfast? I am starving"}],"emotion":"eager"}')
echo "$RESP" | python3 -c "import sys,json;d=json.load(sys.stdin);print('    reply:',d.get('reply','')[:80]);print('    media tags:',d.get('media'))" 2>/dev/null || echo "    raw: $RESP"
echo "$RESP" | grep -q '"media"' && pass "answer returns media field" || fail "no media field"

echo "== mac /api/turn (text, eager) -> reply + media + audio =="
T=$(curl -s -X POST "$APP/api/turn" -F "user_text=What do you have for breakfast?" -F "forced_emotion=eager" -F "messages_json=[]")
echo "$T" | python3 -c "import sys,json;d=json.load(sys.stdin);print('    reply:',d['reply'][:70]);print('    media:',[m['title'] for m in d.get('media',[])]);print('    audio:','yes' if d.get('audio_b64') else 'no')" 2>/dev/null || echo "    raw: $T"
echo "$T" | python3 -c "import sys,json;sys.exit(0 if json.load(sys.stdin).get('media') else 1)" && pass "turn returned media tiles" || fail "turn returned NO media"

echo "== mac /api/turn (distress) -> media suppressed =="
D=$(curl -s -X POST "$APP/api/turn" -F "user_text=Someone is following me and I am scared" -F "forced_emotion=distress" -F "messages_json=[]")
echo "$D" | python3 -c "import sys,json;d=json.load(sys.stdin);print('    media:',d.get('media'));sys.exit(0 if not d.get('media') else 1)" \
  && pass "media suppressed on distress" || fail "media NOT suppressed on distress"

echo ""
[ "$FAILED" = "0" ] && echo "ALL PASSED ✅" || { echo "SOME FAILED ❌"; exit 1; }
