name: Video Auto Dubber

on:
  workflow_dispatch:
    inputs:
      video_url:
        description: 'رابط الفيديو (يوتيوب أو رابط مباشر)'
        required: true
        type: string
      dialect_voice:
        description: 'اختر اللهجة والصوت'
        required: true
        type: choice
        options:
          - 'الدارجة المغربية - صوت رجالي (جمال)'
          - 'الدارجة المغربية - صوت نسائي (منى)'
          - 'العربية الفصحى - صوت رجالي (حامد)'
          - 'العربية الفصحى - صوت نسائي (سلمى)'
        default: 'الدارجة المغربية - صوت رجالي (جمال)'
      expire_time:
        description: 'مدة بقاء الفيديو في المنصة السحابية المؤقتة'
        required: true
        type: choice
        options:
          - '1h'
          - '12h'
          - '24h'
          - '72h'
        default: '24h'

jobs:
  dub-video:
    runs-on: ubuntu-latest
    steps:
      - name: جلب ملفات المستودع
        uses: actions/checkout@v4

      - name: استرجاع المكاتب وموديل الذكاء الاصطناعي من الكاش
        uses: actions/cache@v4
        with:
          path: |
            ~/.cache/pip
            ~/.cache/huggingface
          key: dubber-cache-v2

      - name: تثبيت المتطلبات وتشغيل WARP ومولد PO Token
        run: |
          # تثبيت المكاتب مع تحديد av<14 لتفادي خطأ faster-whisper
          pip install -U faster-whisper "av<14" deep-translator edge-tts pydub "yt-dlp[default,curl-cffi]" bgutil-ytdlp-pot-provider

          # 1. تثبيت FFmpeg و Cloudflare WARP في خطوة واحدة سريعة
          curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | sudo gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
          echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflare-warp.list
          sudo apt-get update && sudo apt-get install -y ffmpeg cloudflare-warp

          # 2. تشغيل WARP أولا على المنفذ 40000
          sudo warp-cli --accept-tos registration new || sudo warp-cli --accept-tos register
          sudo warp-cli --accept-tos mode proxy || sudo warp-cli --accept-tos set-mode proxy
          sudo warp-cli --accept-tos proxy port 40000 || sudo warp-cli --accept-tos set-proxy-port 40000
          sudo warp-cli --accept-tos connect
          sleep 4

          # 3. تشغيل مولد GVS PO Token عبر نفس بروكسي WARP باش يتطابق الـ IP
          docker run -d --name bgutil-pot --network host \
            -e HTTP_PROXY="socks5://127.0.0.1:40000" \
            -e HTTPS_PROXY="socks5://127.0.0.1:40000" \
            brainicism/bgutil-ytdlp-pot-provider
          sleep 2

      - name: تحميل الفيديو المصدر مع فك تشفير EJS
        run: |
          URL="${{ inputs.video_url }}"
          
          echo "المحاولة 1: التحميل عبر WARP مع تطابق PO Token و Chrome Impersonation..."
          yt-dlp --js-runtimes node \
            --remote-components ejs:github \
            --impersonate chrome \
            --proxy "socks5://127.0.0.1:40000" \
            --extractor-args "youtube:player_client=mweb,web,tv" \
            -f "bestvideo[height<=720]+bestaudio/best[height<=720]/best" \
            --merge-output-format mp4 \
            -o "input_video.mp4" "$URL" || true

          if [ ! -s "input_video.mp4" ]; then
            echo "المحاولة 2: تجديد IP ديال WARP والتحميل بصيغة HLS (m3u8)..."
            sudo warp-cli --accept-tos disconnect
            sleep 2
            sudo warp-cli --accept-tos connect
            sleep 4
            yt-dlp --js-runtimes node \
              --remote-components ejs:github \
              --proxy "socks5://127.0.0.1:40000" \
              --extractor-args "youtube:player_client=web_safari,ios,tv,mweb" \
              -f "best[height<=720]/bestvideo[height<=720]+bestaudio/best" \
              --merge-output-format mp4 \
              -o "input_video.mp4" "$URL" || true
          fi

          ls -lh input_video.mp4

      - name: تشغيل الدبلجة حسب اللهجة المختارة
        run: |
          python dubber.py "input_video.mp4" "${{ inputs.dialect_voice }}"

      - name: رفع الفيديو للمشاهدة السحابية المؤقتة
        run: |
          echo "جاري رفع الفيديو إلى Litterbox..."
          STREAM_URL=$(curl -s -F "reqtype=fileupload" -F "time=${{ inputs.expire_time }}" -F "fileToUpload=@dubbed_video.mp4" https://litterbox.catbox.moe/resources/internals/api.php)
          
          echo "### 🎬 الفيديو المدبلج واجد للمشاهدة!" >> $GITHUB_STEP_SUMMARY
          echo "**اللهجة المختارة:** ${{ inputs.dialect_voice }}" >> $GITHUB_STEP_SUMMARY
          echo "**صلاحية الرابط:** ${{ inputs.expire_time }} (يتحذف تلقائيا بعدها)" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "👉 **[إضغط هنا لمشاهدة الفيديو مباشرة في المتصفح بدون تحميل]($STREAM_URL)**" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "الرابط المباشر: \`$STREAM_URL\`" >> $GITHUB_STEP_SUMMARY
