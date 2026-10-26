/**
 * Alfred AI — Lightweight Butler Chat Engine
 * Designed for Ashok (@ashok_a.i)
 */

(function () {
  // Elements
  const heroSection = document.getElementById("hero-section");
  const chatMessages = document.getElementById("chat-messages");
  const chatWorkspace = document.getElementById("chat-workspace");
  const chatInput = document.getElementById("chat-input");
  const sendBtn = document.getElementById("send-btn");
  const actionCards = document.querySelectorAll(".action-card");
  const aiUpdatesBtn = document.getElementById("ai-updates-btn");
  const gIntelBtn = document.getElementById("g-intel-btn");
  const contentMonitorBtn = document.getElementById("content-monitor-btn");
  const customScriptBtn = document.getElementById("custom-script-btn");
  const DEFAULT_PLACEHOLDER = "Type your request to Alfred...";
  let gIntelligence = false; // G-Intelligence on: typed questions go to the connected Google apps
  const SCAN_PROMPT = "Scan the latest 24h AI breaking news across all authorized labs and summarize the top discoveries.";

  // History Drawer
  const historyBtn = document.getElementById("history-btn");
  const newChatBtn = document.getElementById("new-chat-btn");
  const historyDrawer = document.getElementById("history-drawer");
  const closeHistoryBtn = document.getElementById("close-history-btn");
  const drawerBackdrop = document.getElementById("drawer-backdrop");
  const historyContent = document.getElementById("history-content");

  // Tools
  const attachBtn = document.getElementById("attach-btn");
  const voiceBtn = document.getElementById("voice-btn");

  let isGenerating = false;
  let pendingAttachment = null; // { name, text } extracted server-side, sent with the next message

  function init() {
    setupInputEvents();
    setupActionCards();
    setupAiUpdatesButton();
    setupModeChips();
    setupTermPopover();
    setupHistoryDrawer();
    setupToolButtons();
    newChatBtn.addEventListener("click", clearMemory);
    setupProfileMenu();
    setupGoogleButton();
    restoreMemory();
  }

  // Replays the persisted conversation (server-side memory) after a reload
  async function restoreMemory() {
    try {
      const res = await fetch("/api/v1/alfred/memory");
      const memory = await res.json();
      const messages = memory.messages || [];
      if (!messages.length) return;
      if (heroSection) heroSection.classList.add("hidden");
      messages.forEach((m) => appendMessage(m.role, m.content, m));
    } catch (e) {
      console.warn("Could not restore memory:", e);
    }
  }

  // Profile avatar opens a small menu holding Connect Google + History
  function setupProfileMenu() {
    const btn = document.getElementById("profile-btn");
    const menu = document.getElementById("profile-menu");
    const setOpen = (open) => {
      menu.classList.toggle("hidden", !open);
      btn.setAttribute("aria-expanded", String(open));
    };
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      setOpen(menu.classList.contains("hidden"));
    });
    menu.addEventListener("click", (e) => {
      if (e.target.closest(".profile-menu-item")) setOpen(false);
    });
    document.addEventListener("click", (e) => {
      if (!menu.contains(e.target)) setOpen(false);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") setOpen(false);
    });
  }

  async function setupGoogleButton() {
    const btn = document.getElementById("google-btn");
    const label = btn.querySelector("span");
    let connected = false;
    let reconnect = false;
    try {
      const s = await (await fetch("/api/v1/google/status")).json();
      connected = s.connected;
      reconnect = s.needs_reconnect;
    } catch (e) {
      console.warn("Google status unavailable:", e);
    }
    const render = () => {
      label.textContent = reconnect ? "Reconnect Google" : connected ? "Google connected ✓" : "Connect Google";
      btn.title = connected && !reconnect ? "Connected. Click to disconnect" : "Connect your Google account";
    };
    render();
    btn.addEventListener("click", async () => {
      if (!connected || reconnect) {
        window.location.href = "/api/v1/google/connect";
        return;
      }
      // Only an explicit, confirmed click disconnects the account
      if (!confirm("Disconnect your Google account from Alfred?")) return;
      await fetch("/api/v1/google/disconnect", { method: "POST" });
      connected = false;
      render();
    });
  }

  async function clearMemory() {
    if (isGenerating) return;
    try {
      await fetch("/api/v1/alfred/memory", { method: "DELETE" });
    } catch (e) {
      console.warn("Could not clear memory:", e);
    }
    chatMessages.innerHTML = "";
    if (heroSection) heroSection.classList.remove("hidden");
  }

  function setupInputEvents() {
    chatInput.addEventListener("input", function () {
      this.style.height = "auto";
      this.style.height = Math.min(this.scrollHeight, 160) + "px";
      sendBtn.disabled = (this.value.trim().length === 0 && !pendingAttachment) || isGenerating;
    });

    chatInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submitQuery();
      }
    });

    sendBtn.addEventListener("click", submitQuery);
  }

  function setupActionCards() {
    actionCards.forEach((card) => {
      card.addEventListener("click", () => {
        const prompt = card.dataset.prompt;
        if (prompt && !isGenerating) {
          sendPrompt(prompt);
        }
      });
    });
  }

  // "AI Updates" chip in the typing area: runs a fresh 24h news scan + digest
  function setupAiUpdatesButton() {
    aiUpdatesBtn.addEventListener("click", () => {
      if (isGenerating) return;
      aiUpdatesBtn.disabled = true;
      sendPrompt(SCAN_PROMPT, "Alfred is scanning the latest 24h AI news (about 1 minute)...")
        .finally(() => (aiUpdatesBtn.disabled = false));
    });
  }

  // G-Intelligence is a toggle: while on, typed questions are answered from the user's Google apps.
  // Content-Monitor is a placeholder for a later feature.
  function setupModeChips() {
    gIntelBtn.addEventListener("click", () => {
      gIntelligence = !gIntelligence;
      gIntelBtn.classList.toggle("active", gIntelligence);
      gIntelBtn.setAttribute("aria-pressed", String(gIntelligence));
      chatInput.placeholder = gIntelligence ? "Ask about your Drive, Docs, Gmail, Calendar, Photos..." : DEFAULT_PLACEHOLDER;
      chatInput.focus();
    });
    setupScriptPanel();
    contentMonitorBtn.addEventListener("click", () => {
      contentMonitorBtn.classList.add("pulse");
      setTimeout(() => contentMonitorBtn.classList.remove("pulse"), 600);
    });
  }

  // Custom Script: his own words become a HeyGen video (kept verbatim) or an image prompt.
  function setupScriptPanel() {
    const panel = document.getElementById("script-panel");
    const text = document.getElementById("script-text");
    const go = document.getElementById("script-go");
    const aspectRow = document.getElementById("script-aspect");
    let kind = "video";
    const lengthLine = document.getElementById("script-length");
    // His words are kept as written, so the video is as long as the script: show that while he types.
    const updateLength = () => {
      const words = text.value.trim() ? text.value.trim().split(/\s+/).length : 0;
      lengthLine.classList.toggle("hidden", kind !== "video" || !words);
      lengthLine.textContent = `About ${Math.max(1, Math.round(words / WORDS_PER_SECOND))} s of video · ${words} words`;
    };
    text.addEventListener("input", updateLength);
    const setKind = (k) => {
      kind = k;
      panel.querySelectorAll(".script-kind-btn").forEach((b) => b.classList.toggle("active", b.dataset.kind === k));
      aspectRow.classList.toggle("hidden", k !== "image");
      updateLength();
      document.getElementById("script-heygen").classList.toggle("hidden", k !== "video");
      go.textContent = k === "video" ? "Generate video" : "Generate image";
      text.placeholder = k === "video"
        ? "Write exactly what you will say. Alfred keeps your words, splits them into clips and adds the visuals."
        : "Describe the image: subject, setting, style, colours, any text you want on it.";
    };
    panel.querySelectorAll(".script-kind-btn").forEach((b) => b.addEventListener("click", () => setKind(b.dataset.kind)));
    const close = () => panel.classList.add("hidden");
    const heygenLine = document.getElementById("script-heygen");
    const showHeygen = async () => {
      heygenLine.className = "script-heygen";
      heygenLine.textContent = "Checking HeyGen...";
      try {
        const h = await (await fetch("/api/v1/studio/heygen")).json();
        heygenLine.classList.add(h.ready ? "ok" : "bad");
        heygenLine.textContent = h.ready
          ? `HeyGen ready · ${h.avatar_name || "your avatar"}${h.credits != null ? ` · ${h.credits} credits` : ""}`
          : `HeyGen not ready: ${h.reason}`;
      } catch (e) {
        heygenLine.textContent = "Couldn't check HeyGen right now.";
      }
    };
    customScriptBtn.addEventListener("click", () => { panel.classList.remove("hidden"); text.focus(); showHeygen(); });
    document.getElementById("script-close").addEventListener("click", close);
    panel.addEventListener("click", (e) => { if (e.target === panel) close(); });
    go.addEventListener("click", () => {
      const script = text.value.trim();
      if (!script || isGenerating) return;
      close();
      text.value = "";
      updateLength();
      const aspect = document.getElementById("script-aspect-select").value;
      if (kind === "video") sendPrompt(script, "Planning clips from your script...", "custom_video");
      else sendPrompt(script, "Creating your image...", "custom_image", { aspect });
    });
  }

  function setupHistoryDrawer() {
    historyBtn.addEventListener("click", () => {
      historyDrawer.classList.remove("hidden");
      drawerBackdrop.classList.remove("hidden");
      loadSessions(closeDrawer);
    });

    function closeDrawer() {
      historyDrawer.classList.add("hidden");
      drawerBackdrop.classList.add("hidden");
    }

    closeHistoryBtn.addEventListener("click", closeDrawer);
    drawerBackdrop.addEventListener("click", closeDrawer);
  }

  // History = the last 15 chat sessions (saved each time "New chat" is pressed)
  async function loadSessions(closeDrawer) {
    historyContent.innerHTML = `<div class="history-empty">Loading...</div>`;
    let sessions = [];
    try {
      sessions = (await (await fetch("/api/v1/alfred/sessions")).json()).sessions || [];
    } catch (e) {
      historyContent.innerHTML = `<div class="history-empty">Could not load history.</div>`;
      return;
    }
    if (!sessions.length) {
      historyContent.innerHTML = `<div class="history-empty">No past chats yet. Press + (New chat) to save the current chat here.</div>`;
      return;
    }
    historyContent.innerHTML = "";
    sessions.forEach((s) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "history-item-row";
      const when = s.updated_at ? new Date(s.updated_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "";
      row.innerHTML = `<span class="hist-title"></span><span class="hist-meta"></span>`;
      row.querySelector(".hist-title").textContent = s.title;
      row.querySelector(".hist-meta").textContent = `${when} · ${Math.ceil(s.count / 2)} messages`;
      row.addEventListener("click", () => openSession(s.id, closeDrawer));
      historyContent.appendChild(row);
    });
  }

  async function openSession(id, closeDrawer) {
    if (isGenerating) return;
    try {
      const res = await fetch(`/api/v1/alfred/sessions/${encodeURIComponent(id)}/open`, { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const memory = await res.json();
      chatMessages.innerHTML = "";
      const messages = memory.messages || [];
      if (heroSection) heroSection.classList.toggle("hidden", messages.length > 0);
      messages.forEach((m) => appendMessage(m.role, m.content, m));
      closeDrawer();
    } catch (e) {
      historyContent.insertAdjacentHTML("afterbegin", `<div class="history-empty">Could not open chat: ${escapeHtml(e.message)}</div>`);
    }
  }

  function setupToolButtons() {
    // Real file attachment: upload -> server extracts text -> sent with the next message
    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = ".pdf,.docx,.txt,.md,.csv,.json,.py,.js,.ts,.html,.css,.xml,.yaml,.yml,.log";
    fileInput.hidden = true;
    document.body.appendChild(fileInput);

    attachBtn.addEventListener("click", () => {
      if (!isGenerating) fileInput.click();
    });

    fileInput.addEventListener("change", async () => {
      const file = fileInput.files[0];
      fileInput.value = "";
      if (!file) return;
      showAttachmentChip(file.name, "Reading...");
      try {
        const form = new FormData();
        form.append("file", file);
        const res = await fetch("/api/v1/alfred/attach", { method: "POST", body: form });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        pendingAttachment = { name: data.name, text: data.text };
        showAttachmentChip(data.name, data.truncated ? "Attached (first part)" : "Attached");
        sendBtn.disabled = isGenerating;
        chatInput.focus();
      } catch (e) {
        pendingAttachment = null;
        showAttachmentChip(file.name, `Failed: ${e.message}`, true);
      }
    });

    voiceBtn.addEventListener("click", () => {
      chatInput.placeholder = "Listening... (or type your request)";
      setTimeout(() => {
        chatInput.placeholder = "Type your request to Alfred...";
      }, 3000);
    });
  }

  function showAttachmentChip(name, status, isError) {
    let chip = document.getElementById("attachment-chip");
    if (!chip) {
      chip = document.createElement("div");
      chip.id = "attachment-chip";
      chip.className = "attachment-chip";
      document.querySelector(".quick-chips").appendChild(chip);
    }
    chip.classList.toggle("error", !!isError);
    chip.innerHTML = `<span class="attachment-name"></span><span class="attachment-status"></span><button type="button" title="Remove attachment">&times;</button>`;
    chip.querySelector(".attachment-name").textContent = name;
    chip.querySelector(".attachment-status").textContent = status;
    chip.querySelector("button").addEventListener("click", clearAttachment);
  }

  function clearAttachment() {
    pendingAttachment = null;
    const chip = document.getElementById("attachment-chip");
    if (chip) chip.remove();
    sendBtn.disabled = chatInput.value.trim().length === 0 || isGenerating;
  }

  function submitQuery() {
    const text = chatInput.value.trim() || (pendingAttachment ? "Summarize this file." : "");
    if (!text || isGenerating) return;

    chatInput.value = "";
    chatInput.style.height = "auto";
    sendBtn.disabled = true;

    sendPrompt(text, gIntelligence ? "Alfred is checking your Google apps..." : undefined, gIntelligence ? "google" : null);
  }

  async function sendPrompt(text, thinkingLabel, mode = null, extra = {}) {
    if (heroSection) {
      heroSection.classList.add("hidden");
    }

    const attachment = pendingAttachment;
    if (attachment) clearAttachment();
    appendMessage("user", attachment ? `📎 ${attachment.name}\n${text}` : text);

    isGenerating = true;
    sendBtn.disabled = true;
    const thinkingId = appendThinkingBubble(thinkingLabel || "Alfred is on it...");
    const jobId = `job-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    addStopButton(thinkingId, jobId);
    const stopProgress = watchProgress(jobId, thinkingId);

    try {
      const res = await fetch("/api/v1/alfred/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          job_id: jobId,
          attachment_name: attachment ? attachment.name : null,
          attachment_text: attachment ? attachment.text : null,
          mode,
          ...extra,
        }),
      });

      const data = await res.json();
      stopProgress();
      removeElement(thinkingId);
      appendMessage("assistant", data.reply || "At your service, Master Ashok.", data);
    } catch (err) {
      stopProgress();
      removeElement(thinkingId);
      appendMessage(
        "assistant",
        `Forgive me, Master Ashok. A momentary disturbance in our telemetry link occurred: ${err.message}`
      );
    } finally {
      isGenerating = false;
      sendBtn.disabled = chatInput.value.trim().length === 0;
      chatInput.focus();
    }
  }

  // Polls the backend for the job's current step and shows it live: the current step with a
  // spinner, finished steps above it with a check mark (only the last few are kept).
  const WORDS_PER_SECOND = 2.75; // his speaking pace in videos (same as studio.WORDS_PER_SECOND)

  function watchProgress(jobId, bubbleId) {
    let stopped = false;
    let last = null;
    const steps = [];
    const MAX_STEPS = 5;

    async function tick() {
      if (stopped) return;
      try {
        const res = await fetch(`/api/v1/progress/${encodeURIComponent(jobId)}`);
        const { message } = await res.json();
        if (message && message !== last && !stopped) {
          last = message;
          steps.push(message);
          renderSteps();
        }
      } catch (e) {
        /* transient polling error: keep showing the last step */
      }
      if (!stopped) setTimeout(tick, 1200);
    }

    function renderSteps() {
      const body = document.querySelector(`#${bubbleId} .bubble-body`);
      if (!body) return;
      let list = body.querySelector(".progress-steps");
      if (!list) {
        body.querySelector(".thinking-state").remove();
        list = document.createElement("div");
        list.className = "progress-steps";
        const stop = body.querySelector(".stop-btn");
        stop ? body.insertBefore(list, stop) : body.appendChild(list);
      }
      const shown = steps.slice(-MAX_STEPS);
      list.innerHTML = shown.map((s, i) => {
        const current = i === shown.length - 1;
        return `<div class="progress-step${current ? " current" : ""}">${
          current ? '<div class="spinner"></div>' : '<span class="step-check">✓</span>'
        }<span>${escapeHtml(s)}</span></div>`;
      }).join("");
      scrollToBottom();
    }

    setTimeout(tick, 400);
    return () => { stopped = true; };
  }

  function appendMessage(role, text, data) {
    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${role}`;

    const formatted = formatMarkdown(text);

    if (role === "assistant") {
      bubble.innerHTML = `
        <div class="bubble-avatar">
          <img class="avatar-logo-img" src="avatar.png?v=10" alt="Alfred">
        </div>
        <div class="bubble-body">
          <div class="bubble-sender">Alfred</div>
          <div class="bubble-text">${formatted}</div>
        </div>
      `;
    } else {
      bubble.innerHTML = `
        <div class="bubble-body">
          <div class="bubble-text">${escapeHtml(text)}</div>
        </div>
      `;
    }

    if (role === "assistant" && data) {
      renderExtras(bubble.querySelector(".bubble-body"), data);
    }

    chatMessages.appendChild(bubble);
    scrollToBottom();
  }

  // Renders structured payloads (carousel slides, digest buttons) under a reply
  function renderExtras(body, data) {
    if (data.action === "carousel" && Array.isArray(data.slides)) {
      const block = document.createElement("div");
      block.className = "carousel-block";

      const grid = document.createElement("div");
      grid.className = "slide-grid";
      const slideUrls = [...data.slides];
      data.slides.forEach((url, i) => {
        const cell = document.createElement("div");
        cell.className = "slide-cell";
        const link = document.createElement("a");
        link.href = url;
        link.addEventListener("click", (e) => {
          e.preventDefault();
          openLightbox(slideUrls, i);
        });
        const img = document.createElement("img");
        img.src = url;
        img.alt = `Slide ${i + 1}`;
        img.addEventListener("load", scrollToBottom);
        link.appendChild(img);
        cell.appendChild(link);
        addModifyControl(cell, img, link, data.format === "single"
          ? { kind: "single", news_id: data.news_id, brand: data.brand }
          : { kind: "slide", news_id: data.news_id, index: i + 1 }, (u) => { slideUrls[i] = u; });
        grid.appendChild(cell);
      });
      block.appendChild(grid);

      let captionInput = null;
      if (data.caption) {
        // Editable caption: Save stores it on the server and Publish uses it.
        captionInput = document.createElement("textarea");
        captionInput.className = "caption-box caption-edit";
        captionInput.value = data.caption;
        captionInput.rows = Math.min(8, data.caption.split("\n").length + 1);
        block.appendChild(captionInput);
      }

      const actions = document.createElement("div");
      actions.className = "msg-actions";
      if (captionInput) {
        const saveBtn = makeButton("Save caption", false, async (btn) => {
          btn.disabled = true;
          btn.textContent = "Saving...";
          try {
            const res = await fetch(`/api/v1/carousel/${data.news_id}/caption`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ caption: captionInput.value }),
            });
            if (!res.ok) throw new Error((await res.json()).detail || res.status);
            data.caption = captionInput.value;
            btn.textContent = "Saved";
          } catch (e) {
            btn.textContent = "Save failed";
          } finally {
            btn.disabled = false;
          }
        });
        captionInput.addEventListener("input", () => { saveBtn.textContent = "Save caption"; });
        actions.appendChild(saveBtn);
        actions.appendChild(makeButton("Copy caption", false, async (btn) => {
          await navigator.clipboard.writeText(captionInput.value);
          btn.textContent = "Copied";
        }));
      }
      const isSingle = data.format === "single";
      const brand = data.brand === "linkedin" ? "linkedin" : "instagram";
      const platforms = isSingle
        ? (brand === "linkedin" ? [["linkedin", "LinkedIn"]] : [["instagram", "Instagram"]])
        : [["instagram", "Instagram"], ["linkedin", "LinkedIn"], ["youtube", "YouTube Short"]];
      if (isSingle) grid.classList.add("single");
      const picker = document.createElement("div");
      picker.className = "platform-picker";
      platforms.forEach(([value, name]) => {
        const label = document.createElement("label");
        label.className = "platform-option";
        label.innerHTML = `<input type="checkbox" value="${value}" checked> <span>${name}</span>`;
        picker.appendChild(label);
      });

      const status = document.createElement("div");
      status.className = "publish-status";
      const musicPicker = buildMusicPicker();

      const publishBtn = makeButton("Publish", true, async (btn) => {
        const selected = [...picker.querySelectorAll("input:checked")].map((c) => c.value);
        if (!selected.length) {
          status.textContent = "Select at least one platform.";
          return;
        }
        btn.disabled = true;
        btn.textContent = "Publishing...";
        status.textContent = "";
        try {
          const res = await fetch(`/api/v1/carousel/publish/${data.news_id}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ platforms: selected, format: isSingle ? "single" : "carousel", brand,
                                   ...musicPicker.selection() }),
          });
          const results = (await res.json()).results || {};
          status.innerHTML = selected.map((p) => {
            const name = platforms.find(([v]) => v === p)[1];
            const r = results[p] || {};
            if (r.status === "success") {
              return r.url
                ? `${escapeHtml(name)}: posted (<a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">view</a>)`
                : `${escapeHtml(name)}: posted`;
            }
            return `${escapeHtml(name)}: ${escapeHtml(r.message || r.status || "error")}`;
          }).join("<br>");
        } catch (e) {
          status.textContent = `Publish failed: ${e.message}`;
        } finally {
          btn.disabled = false;
          btn.textContent = "Publish";
        }
      });

      actions.appendChild(picker);
      actions.appendChild(publishBtn);
      block.appendChild(musicPicker.el);
      block.appendChild(actions);
      block.appendChild(status);
      const postReceipt = renderUsage(data.usage && { ...data.usage, kind: isSingle ? "image" : "post" });
      if (postReceipt) block.appendChild(postReceipt);
      body.appendChild(block);
    }

    if (data.action === "image" && data.image_url) {
      const block = document.createElement("div");
      block.className = "carousel-block";
      const grid = document.createElement("div");
      grid.className = "slide-grid single";
      const cell = document.createElement("div");
      cell.className = "slide-cell";
      let currentUrl = data.image_url;
      const link = document.createElement("a");
      link.href = data.image_url;
      link.addEventListener("click", (e) => { e.preventDefault(); openLightbox([currentUrl], 0); });
      const img = document.createElement("img");
      img.src = data.image_url;
      img.alt = "Generated image";
      img.addEventListener("load", scrollToBottom);
      link.appendChild(img);
      cell.appendChild(link);
      addModifyControl(cell, img, link, { kind: "studio", file: data.image_url.split("/").pop().split("?")[0] },
        (u) => { currentUrl = u; });
      grid.appendChild(cell);
      block.appendChild(grid);
      const actions = document.createElement("div");
      actions.className = "msg-actions";
      const dl = document.createElement("a");
      dl.className = "msg-action-btn";
      dl.href = data.image_url;
      dl.download = "alfred-image.png";
      dl.textContent = "Download";
      actions.appendChild(dl);
      block.appendChild(actions);
      const receipt = renderUsage(data.usage && { ...data.usage, kind: "image" });
      if (receipt) block.appendChild(receipt);
      body.appendChild(block);
    }

    if (data.action === "video_length" && Array.isArray(data.options)) {
      const row = document.createElement("div");
      row.className = "msg-actions video-length-row";
      data.options.forEach((secs) => {
        row.appendChild(makeButton(`${secs} s`, secs === 30, () => {
          if (isGenerating) return;
          row.querySelectorAll("button").forEach((b) => { b.disabled = true; });
          sendPrompt(`${secs} seconds`, `Writing a ${secs}-second script...`, "video_plan",
            { video_brief: data.video_brief, video_seconds: secs, news_id: data.news_id || null });
        }));
      });
      body.appendChild(row);
    }

    if (data.action === "video_job" && data.job_id) {
      const box = document.createElement("div");
      box.className = "video-job";
      box.innerHTML = `<div class="video-job-status"><span class="video-job-dot"></span><span class="video-job-msg">Starting...</span></div>`;
      body.appendChild(box);
      watchVideoJob(data.job_id, box);
    }

    if (data.action === "workspace" && Array.isArray(data.cards)) {
      const files = data.cards.filter((c) => c.type === "file");
      if (files.length) {
        const actions = document.createElement("div");
        actions.className = "msg-actions";
        files.forEach((f) => {
          const a = document.createElement("a");
          a.className = "msg-action-btn primary";
          a.href = f.download_url;
          a.textContent = `Download ${f.name}`;
          actions.appendChild(a);
        });
        body.appendChild(actions);
      }
    }

    if (data.action === "news_digest" && Array.isArray(data.items) && data.items.length) {
      // Images section: each story first asks for the format (LinkedIn single vs Instagram 4)
      const imgSection = makeSection("Images");
      const choice = document.createElement("div");
      choice.className = "format-choice hidden";
      data.items.forEach((item, i) => {
        imgSection.row.appendChild(makeButton(`Img#${i + 1}`, false, (btn) => {
          imgSection.row.querySelectorAll(".msg-action-btn").forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          choice.innerHTML = `<div class="format-choice-title">#${i + 1} — choose a format</div>`;
          const opts = document.createElement("div");
          opts.className = "msg-actions";
          // Single photo first asks where it will be posted: Instagram is branded, LinkedIn is content only
          const brandRow = document.createElement("div");
          brandRow.className = "msg-actions hidden-row";
          brandRow.innerHTML = `<div class="format-choice-title brand-title">Single photo — is this for Instagram or LinkedIn?</div>`;
          brandRow.appendChild(makeButton("Instagram · @ashok_a.i", false, () => {
            if (!isGenerating) sendPrompt(`generate single image for instagram ${i + 1}`);
          }));
          brandRow.appendChild(makeButton("LinkedIn · Ashokachari P", false, () => {
            if (!isGenerating) sendPrompt(`generate single image for linkedin ${i + 1}`);
          }));

          opts.appendChild(makeButton("Single photo", false, (b) => {
            opts.querySelectorAll(".msg-action-btn").forEach((x) => x.classList.remove("active"));
            b.classList.add("active");
            brandRow.classList.remove("hidden-row");
            scrollToBottom();
          }));
          opts.appendChild(makeButton("4 photos · Instagram", false, () => {
            if (!isGenerating) sendPrompt(`generate images for ${i + 1}`);
          }));
          choice.appendChild(opts);
          choice.appendChild(brandRow);
          choice.classList.remove("hidden");
          scrollToBottom();
        }));
      });
      imgSection.el.appendChild(choice);
      body.appendChild(imgSection.el);

      const vidSection = makeSection("Video");
      data.items.forEach((item, i) => {
        vidSection.row.appendChild(makeButton(`Vid#${i + 1}`, false, () => {
          if (!isGenerating) sendPrompt(`generate video for ${i + 1}`);
        }));
      });
      body.appendChild(vidSection.el);
      imgSection.row.classList.add("num-row");
      vidSection.row.classList.add("num-row");

      highlightTerms(body.querySelector(".bubble-text"), data.items.flatMap((it) => it.terms || []));
    }
  }

  // ----- Highlighted terms: tap a word to see its meaning in English + Telugu -----
  function highlightTerms(container, terms) {
    const unique = [...new Set(terms.map((t) => t.trim()).filter((t) => t.length > 1))]
      .sort((a, b) => b.length - a.length);
    if (!container || !unique.length) return;
    // Match word forms too: "Vulnerability" also finds "vulnerabilities", "Penetration Testing" finds "penetration tester".
    const stem = (w) => {
      const esc = w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      if (w.length < 5) return esc;
      const root = w.replace(/(ies|ing|ers|er|es|ed|s|y)$/i, "");
      return root.length >= 4 ? `${root.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\w*` : esc;
    };
    const variants = unique.map((t) => t.split(/[\s-]+/).map(stem).join("[\\s-]+"));
    const pattern = new RegExp(`\\b(${variants.join("|")})\\b`, "gi");
    const seen = new Set();
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach((node) => {
      const text = node.nodeValue;
      pattern.lastIndex = 0;
      if (!pattern.test(text)) return;
      const frag = document.createDocumentFragment();
      let last = 0;
      text.replace(pattern, (match, _g, offset) => {
        const key = match.toLowerCase();
        frag.appendChild(document.createTextNode(text.slice(last, offset)));
        if (seen.has(key)) {
          frag.appendChild(document.createTextNode(match)); // highlight each term once per digest
        } else {
          seen.add(key);
          const span = document.createElement("span");
          span.className = "term";
          span.textContent = match;
          span.dataset.term = match;
          span.dataset.context = text.slice(Math.max(0, offset - 120), offset + match.length + 120);
          frag.appendChild(span);
        }
        last = offset + match.length;
        return match;
      });
      frag.appendChild(document.createTextNode(text.slice(last)));
      node.parentNode.replaceChild(frag, node);
    });
  }

  const termCache = {};
  function setupTermPopover() {
    const pop = document.createElement("div");
    pop.className = "term-popover hidden";
    pop.innerHTML = `<div class="term-popover-head"><span class="term-popover-title"></span><button type="button" aria-label="Close">&times;</button></div>
      <div class="term-popover-meaning"></div><div class="term-popover-telugu"></div>`;
    document.body.appendChild(pop);
    const hide = () => pop.classList.add("hidden");
    pop.querySelector("button").addEventListener("click", hide);
    document.addEventListener("click", async (e) => {
      const term = e.target.closest(".term");
      if (!term) {
        if (!e.target.closest(".term-popover")) hide();
        return;
      }
      e.preventDefault();
      const word = term.dataset.term;
      pop.querySelector(".term-popover-title").textContent = word;
      pop.querySelector(".term-popover-meaning").textContent = "Looking it up...";
      pop.querySelector(".term-popover-telugu").textContent = "";
      const r = term.getBoundingClientRect();
      const width = Math.min(320, window.innerWidth - 24);
      pop.style.width = `${width}px`;
      pop.style.left = `${Math.max(12, Math.min(r.left, window.innerWidth - width - 12))}px`;
      pop.style.top = `${r.bottom + window.scrollY + 8}px`;
      pop.classList.remove("hidden");
      try {
        const key = word.toLowerCase();
        termCache[key] = termCache[key] || await (await fetch(
          `/api/v1/define?term=${encodeURIComponent(word)}&context=${encodeURIComponent(term.dataset.context || "")}`)).json();
        if (pop.querySelector(".term-popover-title").textContent !== word) return; // another word was tapped
        pop.querySelector(".term-popover-meaning").textContent = termCache[key].meaning || "No meaning found.";
        pop.querySelector(".term-popover-telugu").textContent = termCache[key].telugu || "";
      } catch (err) {
        pop.querySelector(".term-popover-meaning").textContent = "Couldn't load the meaning right now.";
      }
    });
    window.addEventListener("scroll", hide, true);
  }

  // Receipt under a finished image/video: each paid item, total per service, and what's left in each account.
  const BALANCE_HINT = { "Google Gemini": "free tier", Pollinations: "free" };
  function renderUsage(usage) {
    if (!usage || !usage.items) return null;
    const money = (v) => `$${Number(v || 0).toFixed(v > 0 && v < 0.1 ? 3 : 2)}`;
    const el = document.createElement("div");
    el.className = "usage-card";
    const rows = usage.items.map((it) => `<tr><td class="usage-svc">${escapeHtml(it.service)}</td>
      <td>${escapeHtml(it.item)}${it.note ? ` <span class="usage-note">(${escapeHtml(it.note)})</span>` : ""}</td>
      <td class="usage-usd">${money(it.usd)}</td></tr>`).join("");
    const services = Object.entries(usage.per_service || {}).map(([svc, usd]) => {
      const left = (usage.balances || {})[svc];
      const remaining = left != null ? `${money(left)} left` : (BALANCE_HINT[svc] || "");
      return `<tr><td class="usage-svc">${escapeHtml(svc)}</td><td class="usage-usd">${money(usd)}</td>
        <td class="usage-left">${escapeHtml(remaining)}</td></tr>`;
    }).join("");
    const others = Object.entries(usage.balances || {}).filter(([svc, v]) => v != null && !(svc in (usage.per_service || {})))
      .map(([svc, v]) => `${escapeHtml(svc)} ${money(v)} left`).join(" · ");
    el.innerHTML = `<div class="usage-title">Cost of this ${usage.kind || "render"} · <b>${money(usage.total_usd)}</b></div>
      ${usage.items.length ? `<table class="usage-table">${rows}</table>
      <div class="usage-sub">Per service</div><table class="usage-table">${services}</table>`
        : `<div class="usage-note">No paid service was used.</div>`}
      ${others ? `<div class="usage-other">${others}</div>` : ""}`;
    return el;
  }

  // Playful words per stage of a render (rotated like Claude's "Noodling..."), shown with the overall %.
  const STAGE_WORDS = {
    starting: ["Warming up", "Stretching", "Booting up"],
    imagining: ["Imagining", "Daydreaming", "Sketching", "Doodling", "Envisioning"],
    animating: ["Animating", "Noodling", "Wiggling pixels", "Choreographing", "Conjuring"],
    recording: ["Booping", "Rehearsing", "Clearing throat", "Lip-syncing", "Percolating", "Mulling", "Brewing"],
    polishing: ["Polishing", "Steadying", "Buffing", "Smoothing"],
    stitching: ["Stitching", "Splicing", "Weaving", "Assembling"],
    finishing: ["Finishing", "Wrapping up", "Sprinkling captions", "Tying the bow"],
  };

  function renderProgress(box, job, state) {
    let el = box.querySelector(".render-progress");
    if (!el) {
      el = document.createElement("div");
      el.className = "render-progress";
      el.innerHTML = `<div class="render-line"><span class="render-glyph">✻</span>
        <span class="render-word"></span><span class="render-pct"></span>
        <button type="button" class="stop-btn small"><span class="stop-square"></span>Cancel</button></div>
        <div class="render-bar"><div class="render-fill"></div></div>`;
      el.querySelector(".stop-btn").addEventListener("click", async (e) => {
        const b = e.currentTarget;
        b.disabled = true;
        b.lastChild.textContent = "Cancelling…";
        try { await fetch(`/api/v1/studio/jobs/${state.jobId}/cancel`, { method: "POST" }); } catch (err) { /* next poll shows it */ }
      });
      box.querySelector(".video-job-status").replaceWith(el);
    }
    const pct = Math.max(0, Math.min(100, job.progress || 0));
    el.querySelector(".render-pct").textContent = `${pct}%`;
    el.querySelector(".render-fill").style.width = `${pct}%`;
    const stage = STAGE_WORDS[job.stage] ? job.stage : "starting";
    if (state.stage !== stage) {
      state.stage = stage;
      state.i = 0;
      el.querySelector(".render-word").textContent = `${STAGE_WORDS[stage][0]}…`;
    }
    if (!state.timer) {
      state.timer = setInterval(() => {
        const list = STAGE_WORDS[state.stage];
        state.i = (state.i + 1) % list.length;
        const w = box.querySelector(".render-word");
        if (w) w.textContent = `${list[state.i]}…`;
      }, 2600);
    }
  }

  // "Modify" under an image: his words + this image go to the image model; the new version replaces it
  // in place (publishing then uses it). `payload` says which image (slide N, single, or studio file).
  function addModifyControl(cell, img, link, payload, onUrl) {
    const form = document.createElement("div");
    form.className = "modify-form hidden";
    form.innerHTML = `<textarea rows="2" placeholder="What should change? e.g. make the background dark blue, add the Claude logo"></textarea>
      <div class="modify-actions"></div><div class="modify-status"></div>`;
    const input = form.querySelector("textarea");
    const status = form.querySelector(".modify-status");
    const toggle = makeButton("✎ Modify", false, () => {
      form.classList.toggle("hidden");
      if (!form.classList.contains("hidden")) input.focus();
    });
    toggle.classList.add("modify-btn");
    const apply = makeButton("Apply", true, async (btn) => {
      const instruction = input.value.trim();
      if (!instruction) { status.textContent = "Write what to change first."; return; }
      btn.disabled = true;
      btn.textContent = "Modifying…";
      status.textContent = "The image model is making your change (about 20-40 s)…";
      try {
        const res = await (await fetch("/api/v1/images/modify", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...payload, instruction }),
        })).json();
        if (res.status === "success") {
          img.src = res.url;
          if (link) link.href = res.url;
          if (onUrl) onUrl(res.url);
          status.textContent = "Updated. Publishing will use this version.";
          input.value = "";
        } else {
          status.textContent = res.message || res.detail || "Couldn't modify the image.";
        }
        const old = form.querySelector(".usage-card");
        if (old) old.remove();
        const receipt = renderUsage(res.usage && { ...res.usage, kind: "edit" });
        if (receipt) form.appendChild(receipt);
      } catch (e) {
        status.textContent = `Modify failed: ${e.message}`;
      } finally {
        btn.disabled = false;
        btn.textContent = "Apply";
      }
    });
    form.querySelector(".modify-actions").appendChild(apply);
    cell.appendChild(toggle);
    cell.appendChild(form);
  }

  // Background music for image posts: Instagram's library (attached inside Instagram as a Reel) and
  // royalty-free tracks (YouTube, and Instagram when no Instagram track is chosen). One pick per tab.
  const MUSIC_TABS = {
    instagram: { label: "Instagram trending",
      note: "Attached inside Instagram: the Instagram post becomes a Reel slideshow with this track. Not used on YouTube or LinkedIn." },
    royalty_free: { label: "Royalty-free",
      note: "Safe on every platform: used for the YouTube Short (and Instagram if no Instagram track is picked). A credit line is added to the caption." },
  };
  const fmtTime = (s) => `${Math.floor((s || 0) / 60)}:${String(Math.round((s || 0) % 60)).padStart(2, "0")}`;
  let musicAudio = null;
  function buildMusicPicker() {
    const el = document.createElement("div");
    el.className = "music-picker";
    el.innerHTML = `<div class="msg-actions"><button type="button" class="msg-action-btn music-toggle">🎵 Background music</button>
        <span class="music-chosen"></span></div>
      <div class="music-panel hidden">
        <div class="music-tabs">${Object.entries(MUSIC_TABS).map(([k, t]) =>
          `<button type="button" data-src="${k}">${t.label}</button>`).join("")}</div>
        <div class="music-note"></div>
        <div class="music-list"></div>
      </div>`;
    const panel = el.querySelector(".music-panel");
    const list = el.querySelector(".music-list");
    const chosen = el.querySelector(".music-chosen");
    const picks = { instagram: null, royalty_free: null };
    const cache = {};
    let tab = "instagram";
    musicAudio = musicAudio || new Audio();
    const stop = () => { musicAudio.pause(); el.querySelectorAll(".music-play.playing").forEach((b) => { b.classList.remove("playing"); b.textContent = "▶"; }); };
    const showChosen = () => {
      const parts = [];
      if (picks.instagram) parts.push(`Instagram: ${picks.instagram.title}`);
      if (picks.royalty_free) parts.push(`${picks.instagram ? "YouTube" : "All"}: ${picks.royalty_free.title}`);
      chosen.textContent = parts.join(" · ");
    };
    const render = (items) => {
      list.innerHTML = "";
      if (!items.length) { list.textContent = "No tracks right now. Try again in a minute."; return; }
      items.forEach((m) => {
        const row = document.createElement("div");
        row.className = "music-row" + (picks[tab] && picks[tab].id === m.id ? " selected" : "");
        row.innerHTML = `<button type="button" class="music-play" aria-label="Preview">▶</button>
          <div class="music-info"><div class="music-title">${escapeHtml(m.title)}</div>
          <div class="music-meta">${escapeHtml(m.artist || "")} · ${fmtTime(m.duration)}${m.mood ? ` · ${escapeHtml(m.mood)}` : ""}</div></div>`;
        row.querySelector(".music-play").addEventListener("click", (e) => {
          e.stopPropagation();
          const btn = e.currentTarget;
          const wasPlaying = btn.classList.contains("playing");
          stop();
          if (wasPlaying) return;
          musicAudio.src = m.preview_url;
          musicAudio.play().catch(() => { btn.textContent = "✕"; });
          btn.classList.add("playing");
          btn.textContent = "❚❚";
        });
        row.addEventListener("click", () => {
          picks[tab] = picks[tab] && picks[tab].id === m.id ? null : m;  // tap again to unselect
          list.querySelectorAll(".music-row").forEach((r) => r.classList.remove("selected"));
          if (picks[tab]) row.classList.add("selected");
          showChosen();
        });
        list.appendChild(row);
      });
    };
    const open = async (src) => {
      tab = src;
      stop();
      el.querySelectorAll(".music-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.src === src));
      el.querySelector(".music-note").textContent = MUSIC_TABS[src].note;
      if (!cache[src]) {
        list.textContent = src === "instagram" ? "Loading Instagram tracks… (the first load can take a minute)" : "Loading…";
        try {
          cache[src] = (await (await fetch(`/api/v1/music?source=${src}`)).json()).items || [];
        } catch (e) { cache[src] = []; }
      }
      if (tab === src) render(cache[src]);
    };
    el.querySelectorAll(".music-tabs button").forEach((b) => b.addEventListener("click", () => open(b.dataset.src)));
    el.querySelector(".music-toggle").addEventListener("click", () => {
      panel.classList.toggle("hidden");
      if (panel.classList.contains("hidden")) stop(); else open(tab);
    });
    return { el, selection: () => ({ ig_music_id: picks.instagram ? picks.instagram.id : null,
                                      music_id: picks.royalty_free ? picks.royalty_free.id : null }) };
  }

  // Small avatar tiles shown before Render: his HeyGen looks, the current one selected first.
  function buildAvatarPicker() {
    const el = document.createElement("div");
    el.className = "avatar-picker";
    el.innerHTML = `<div class="avatar-picker-label">Loading your avatars…</div><div class="avatar-strip"></div>`;
    const label = el.querySelector(".avatar-picker-label");
    const strip = el.querySelector(".avatar-strip");
    let selected = null;
    fetch("/api/v1/studio/avatars").then((r) => r.json()).then(({ avatars = [] }) => {
      if (!avatars.length) { el.remove(); return; }
      const current = avatars.find((a) => a.current) || avatars[0];
      selected = current.id;
      label.textContent = `Avatar: ${current.name}`;
      [current, ...avatars.filter((a) => a !== current)].forEach((a) => {
        const tile = document.createElement("button");
        tile.type = "button";
        tile.className = "avatar-tile" + (a.id === selected ? " selected" : "");
        tile.title = a.name;
        tile.setAttribute("aria-label", a.name);
        tile.innerHTML = a.thumb ? `<img src="${a.thumb}" alt="" loading="lazy">` : `<span>${escapeHtml(a.name[0] || "?")}</span>`;
        tile.addEventListener("click", () => {
          selected = a.id;
          strip.querySelectorAll(".avatar-tile").forEach((t) => t.classList.toggle("selected", t === tile));
          label.textContent = `Avatar: ${a.name}`;
        });
        strip.appendChild(tile);
      });
    }).catch(() => el.remove());
    return { el, selected: () => selected };
  }

  // Under a finished video: editable caption + Instagram Reel / LinkedIn / YouTube Short + Publish.
  const VIDEO_PLATFORMS = [["instagram", "Instagram Reel"], ["linkedin", "LinkedIn"], ["youtube", "YouTube Short"]];
  function buildVideoPublish(jobId, job) {
    const wrap = document.createElement("div");
    wrap.className = "video-publish";
    const caption = document.createElement("textarea");
    caption.className = "caption-box caption-edit";
    caption.value = job.caption || "";
    caption.rows = Math.min(8, (job.caption || "").split("\n").length + 1);
    caption.placeholder = "Caption for the post";
    wrap.appendChild(caption);

    const picker = document.createElement("div");
    picker.className = "platform-picker";
    VIDEO_PLATFORMS.forEach(([value, name]) => {
      const label = document.createElement("label");
      label.className = "platform-option";
      label.innerHTML = `<input type="checkbox" value="${value}"> <span>${name}</span>`;
      picker.appendChild(label);
    });
    const status = document.createElement("div");
    status.className = "publish-status";
    const showLinks = (published) => Object.entries(published || {}).map(([p, r]) => {
      const name = (VIDEO_PLATFORMS.find(([v]) => v === p) || [p, p])[1];
      return r.url ? `${escapeHtml(name)}: posted (<a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">view</a>)`
        : `${escapeHtml(name)}: posted`;
    });
    status.innerHTML = showLinks(job.published).join("<br>");

    const actions = document.createElement("div");
    actions.className = "msg-actions";
    actions.appendChild(picker);
    actions.appendChild(makeButton("Publish", true, async (btn) => {
      const selected = [...picker.querySelectorAll("input:checked")].map((c) => c.value);
      if (!selected.length) {
        status.textContent = "Select where to post it.";
        return;
      }
      btn.disabled = true;
      btn.textContent = "Publishing…";
      status.textContent = "Uploading the video. LinkedIn and YouTube can take a minute.";
      try {
        const res = await fetch(`/api/v1/studio/jobs/${jobId}/publish`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ platforms: selected, caption: caption.value }),
        });
        const results = (await res.json()).results || {};
        status.innerHTML = selected.map((p) => {
          const name = VIDEO_PLATFORMS.find(([v]) => v === p)[1];
          const r = results[p] || {};
          if (r.status === "success") {
            return r.url ? `${escapeHtml(name)}: posted (<a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">view</a>)`
              : `${escapeHtml(name)}: posted`;
          }
          return `${escapeHtml(name)}: ${escapeHtml(r.message || r.status || "error")}`;
        }).join("<br>");
      } catch (e) {
        status.textContent = `Publish failed: ${e.message}`;
      } finally {
        btn.disabled = false;
        btn.textContent = "Publish";
      }
    }));
    actions.appendChild(makeButton("Copy caption", false, async (btn) => {
      await navigator.clipboard.writeText(caption.value);
      btn.textContent = "Copied";
    }));
    wrap.appendChild(actions);
    wrap.appendChild(status);
    return wrap;
  }

  // Polls a HeyGen render job until the 9:16 video is ready, then shows the player.
  function watchVideoJob(jobId, box) {
    const state = { stage: null, i: 0, timer: null, jobId };
    const stopWords = () => { if (state.timer) clearInterval(state.timer); state.timer = null; };
    // The plain status line (used for "ready to render" and errors); brings it back if the progress bar replaced it.
    const statusMsg = () => {
      const bar = box.querySelector(".render-progress");
      if (bar) {
        bar.insertAdjacentHTML("beforebegin",
          `<div class="video-job-status"><span class="video-job-dot"></span><span class="video-job-msg"></span></div>`);
        bar.remove();
      }
      return box.querySelector(".video-job-msg");
    };
    const tick = async () => {
      try {
        const job = await (await fetch(`/api/v1/studio/jobs/${jobId}`)).json();
        if (job.status === "done" && job.url) {
          stopWords();
          box.innerHTML = `<video class="video-job-player" controls playsinline preload="metadata" src="${job.url}"></video>
            <div class="msg-actions"><a class="msg-action-btn" href="${job.url}" download="alfred-video.mp4">Download video</a></div>`;
          box.appendChild(buildVideoPublish(jobId, job));
          const receipt = renderUsage(job.usage && { ...job.usage, kind: "video" });
          if (receipt) box.appendChild(receipt);
          scrollToBottom();
          return;
        }
        if (job.status === "failed" || job.detail || job.status === "cancelled") {
          stopWords();
          box.classList.add("failed");
          statusMsg().textContent = job.message || job.detail || "The video failed.";
          const spent = job.usage && job.usage.total_usd > 0 && renderUsage({ ...job.usage,
            kind: job.status === "cancelled" ? "cancelled render (already charged)" : "failed render (already charged)" });
          if (spent) box.appendChild(spent);
          return;
        }
        if (job.status === "awaiting_approval") {
          // Nothing paid runs until he confirms.
          const est = job.estimate || {};
          statusMsg().textContent = `Ready to render · about $${est.total_usd ?? "?"} (HeyGen $${est.heygen_usd ?? "?"} + top half $${est.content_usd ?? "?"})`;
          if (!box.querySelector(".video-job-actions")) {
            const avatars = buildAvatarPicker();
            box.appendChild(avatars.el);
            const row = document.createElement("div");
            row.className = "msg-actions video-job-actions";
            const go = makeButton("Render video", true, async (btn) => {
              btn.disabled = true;
              btn.textContent = "Starting...";
              await fetch(`/api/v1/studio/jobs/${jobId}/approve`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ avatar_id: avatars.selected() }),
              });
              row.remove();
              avatars.el.remove();
              tick();
            });
            const cancel = makeButton("Cancel", false, async () => {
              await fetch(`/api/v1/studio/jobs/${jobId}/cancel`, { method: "POST" });
              row.remove();
              avatars.el.remove();
              tick();
            });
            row.appendChild(go);
            row.appendChild(cancel);
            box.appendChild(row);
          }
          return; // stop polling until he decides
        }
        renderProgress(box, job, state);
      } catch (e) {
        /* transient polling error: keep showing the last state */
      }
      setTimeout(tick, 4000);
    };
    tick();
  }

  function makeSection(title) {
    const el = document.createElement("div");
    el.className = "digest-section";
    el.innerHTML = `<div class="digest-section-title">${escapeHtml(title)}</div>`;
    const row = document.createElement("div");
    row.className = "msg-actions";
    el.appendChild(row);
    return { el, row };
  }

  // In-page slide viewer with prev/next arrows and keyboard support
  const lightbox = { el: null, img: null, counter: null, slides: [], index: 0 };

  function buildLightbox() {
    const el = document.createElement("div");
    el.className = "lightbox hidden";
    el.innerHTML = `
      <button class="lightbox-close" title="Close (Esc)">&times;</button>
      <button class="lightbox-nav prev" title="Previous (Left arrow)">&#8249;</button>
      <img class="lightbox-img" alt="Slide preview">
      <button class="lightbox-nav next" title="Next (Right arrow)">&#8250;</button>
      <div class="lightbox-counter"></div>
    `;
    document.body.appendChild(el);
    lightbox.el = el;
    lightbox.img = el.querySelector(".lightbox-img");
    lightbox.counter = el.querySelector(".lightbox-counter");
    el.querySelector(".lightbox-close").addEventListener("click", closeLightbox);
    el.querySelector(".prev").addEventListener("click", () => stepLightbox(-1));
    el.querySelector(".next").addEventListener("click", () => stepLightbox(1));
    el.addEventListener("click", (e) => {
      if (e.target === el) closeLightbox();
    });
    document.addEventListener("keydown", (e) => {
      if (el.classList.contains("hidden")) return;
      if (e.key === "Escape") closeLightbox();
      if (e.key === "ArrowLeft") stepLightbox(-1);
      if (e.key === "ArrowRight") stepLightbox(1);
    });
  }

  function openLightbox(slides, index) {
    if (!lightbox.el) buildLightbox();
    lightbox.slides = slides;
    lightbox.index = index;
    renderLightbox();
    lightbox.el.classList.remove("hidden");
  }

  function closeLightbox() {
    lightbox.el.classList.add("hidden");
  }

  function stepLightbox(delta) {
    const n = lightbox.slides.length;
    lightbox.index = (lightbox.index + delta + n) % n;
    renderLightbox();
  }

  function renderLightbox() {
    const n = lightbox.slides.length;
    lightbox.img.src = lightbox.slides[lightbox.index];
    lightbox.counter.textContent = `${lightbox.index + 1} / ${n}`;
    lightbox.el.querySelectorAll(".lightbox-nav").forEach((b) => (b.hidden = n < 2));
  }

  function makeButton(label, primary, onClick) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "msg-action-btn" + (primary ? " primary" : "");
    btn.textContent = label;
    btn.addEventListener("click", () => onClick(btn));
    return btn;
  }

  // Stop button on the "working..." bubble: the server ends the request and replies "Stopped".
  function addStopButton(bubbleId, jobId) {
    const body = document.querySelector(`#${bubbleId} .bubble-body`);
    if (!body) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "stop-btn";
    btn.innerHTML = `<span class="stop-square"></span>Stop`;
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.lastChild.textContent = "Stopping…";
      try {
        await fetch(`/api/v1/progress/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
      } catch (e) { /* the chat request itself will report */ }
    });
    body.appendChild(btn);
  }

  function appendThinkingBubble(label = "Alfred is formulating response...") {
    const id = "thinking-" + Date.now();
    const bubble = document.createElement("div");
    bubble.id = id;
    bubble.className = "chat-bubble assistant";
    bubble.innerHTML = `
      <div class="bubble-avatar">
        <img class="avatar-logo-img" src="avatar.png?v=10" alt="Alfred">
      </div>
      <div class="bubble-body">
        <div class="bubble-sender">Alfred</div>
        <div class="thinking-state">
          <div class="spinner"></div>
          <span>${escapeHtml(label)}</span>
        </div>
      </div>
    `;
    chatMessages.appendChild(bubble);
    scrollToBottom();
    return id;
  }

  function removeElement(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
  }

  function scrollToBottom() {
    chatWorkspace.scrollTop = chatWorkspace.scrollHeight;
  }

  function formatMarkdown(text) {
    if (!text) return "";
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/^#{1,4}\s+(.+)$/gm, "<strong>$1</strong>")
      .replace(/^\s*[*-]\s+/gm, "• ")
      // Bold only for headings / titles / sub-headings: a whole bold line (optionally followed by [..]),
      // or a bold "Label:" at the start of a line. Bold words inside sentences become plain text.
      .replace(/^(\W{0,3}\s*)\*\*([^*\n]+?)\*\*(\s*(\[[^\]\n]*\])?\s*)$/gm, "$1<strong>$2</strong>$3")
      .replace(/^(\W{0,3}\s*)\*\*([^*\n]{1,40}:)\*\*/gm, "$1<strong>$2</strong>")
      .replace(/\*\*(.*?)\*\*/g, "$1")
      .replace(/\*(.*?)\*/g, "<em>$1</em>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/(?:^|\n)\s*---\s*(?:\n|$)/g, '<hr class="news-divider">')
      .replace(/\n\n/g, "</p><p>")
      .replace(/\n/g, "<br>");
  }

  function escapeHtml(str) {
    if (!str) return "";
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  document.addEventListener("DOMContentLoaded", init);
})();
