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
  const DEFAULT_PLACEHOLDER = "Type your request to Alfred...";
  let gIntelligence = false; // G-Intelligence on: typed questions go to the connected Google apps

  // History Drawer
  const historyBtn = document.getElementById("history-btn");
  const newChatBtn = document.getElementById("new-chat-btn");
  const historyDrawer = document.getElementById("history-drawer");
  const closeHistoryBtn = document.getElementById("close-history-btn");
  const drawerBackdrop = document.getElementById("drawer-backdrop");
  const historyContent = document.getElementById("history-content");

  // Tools
  const attachBtn = document.getElementById("attach-btn");

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
    restoreMemory().then(openSharedPost);
  }

  // A post shared to the phone app arrives as ?share=<text/link>&share_image=<id>: Alfred reads it and asks what to make.
  function openSharedPost() {
    const params = new URLSearchParams(location.search);
    const text = (params.get("share") || "").trim();
    const image = params.get("share_image") || "";
    if (!text && !image) return;
    history.replaceState(null, "", location.pathname);  // a reload must not share it again
    if (heroSection) heroSection.classList.add("hidden");
    sendPrompt(`📤 Shared post${text ? `\n${text}` : " (screenshot)"}`, "Reading the shared post...", "shared_post",
      { script: text, share_image: image || null });
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
  // What's left in each paid service, in rupees (refreshed every time the menu opens; the server caches 2 min).
  async function loadBalances() {
    const list = document.getElementById("balance-list");
    try {
      const data = await (await fetch("/api/v1/balances")).json();
      list.innerHTML = "";
      (data.services || []).forEach((sv) => {
        const row = document.createElement("a");
        row.className = "balance-row";
        row.href = sv.link || "#";
        row.target = "_blank";
        row.rel = "noopener";
        const amount = sv.inr != null ? `₹${Math.round(sv.inr).toLocaleString("en-IN")}` : (sv.note || "—");
        row.innerHTML = `<span class="balance-name"><b></b><small></small></span><span class="balance-amount"></span>`;
        row.querySelector("b").textContent = sv.name;
        row.querySelector("small").textContent = sv.use;
        const amt = row.querySelector(".balance-amount");
        amt.textContent = amount;
        if (sv.inr == null) amt.classList.add("muted");
        else if (sv.inr < 100) amt.classList.add("low");  // under ₹100: time to top up
        list.appendChild(row);
      });
      document.getElementById("balance-updated").textContent = data.inr_rate ? `· $1 = ₹${data.inr_rate.toFixed(2)}` : "";
    } catch (e) {
      list.innerHTML = `<div class="balance-row muted">Couldn't load balances</div>`;
    }
  }

  function setupProfileMenu() {
    const btn = document.getElementById("profile-btn");
    const menu = document.getElementById("profile-menu");
    const setOpen = (open) => {
      menu.classList.toggle("hidden", !open);
      btn.setAttribute("aria-expanded", String(open));
    };
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const opening = menu.classList.contains("hidden");
      setOpen(opening);
      if (opening) loadBalances();
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
        if (isGenerating) return;
        const action = card.dataset.action;
        if (action === "updates") return aiUpdatesBtn.click();
        if (action === "monitor") return contentMonitorBtn.click();
        if (card.dataset.fill) {  // start the sentence for him; he finishes it and presses send
          chatInput.value = card.dataset.fill;
          chatInput.focus();
          chatInput.setSelectionRange(chatInput.value.length, chatInput.value.length);
          chatInput.dispatchEvent(new Event("input"));
          return;
        }
        if (card.dataset.prompt) sendPrompt(card.dataset.prompt);
      });
    });
  }

  // "AI Updates" chip in the typing area: asks where to look (official feeds or Google Search), then scans only that
  function setupAiUpdatesButton() {
    aiUpdatesBtn.addEventListener("click", () => {
      if (isGenerating) return;
      if (heroSection) heroSection.classList.add("hidden");
      appendMessage("assistant", "Where should I look for the latest AI news, Master Ashok?", { action: "news_source" });
      scrollToBottom();
    });
  }

  const NEWS_SOURCES = {
    official: { label: "Official news", sub: "21 AI labs, research & tech sites", prompt: "AI Updates: official news",
                wait: "Reading the official AI lab and tech feeds (about 1 minute)..." },
    google: { label: "Google search", sub: "9 areas: AI, gadgets, viral, India, creators", prompt: "AI Updates: Google search",
              wait: "Searching Google for today's AI news (about 1 minute)..." },
  };

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
    setupMonitorPanel();
  }

  // Content-Monitor: his creator list + "Scan now" -> a brief of their breakout posts from the last 3 days.
  function setupMonitorPanel() {
    const panel = document.getElementById("monitor-panel");
    const text = document.getElementById("monitor-text");
    const saveBtn = document.getElementById("monitor-save");
    const handles = () => text.value.split(/[\s,]+/).map((h) => h.trim()).filter(Boolean);
    const save = async () => {
      const res = await fetch("/api/v1/monitor/creators", {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ creators: handles() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      text.value = data.creators.map((c) => `@${c}`).join("\n");
      return data.creators;
    };
    const close = () => panel.classList.add("hidden");
    contentMonitorBtn.addEventListener("click", async () => {
      panel.classList.remove("hidden");
      try {
        const data = await (await fetch("/api/v1/monitor/creators")).json();
        text.value = (data.creators || []).map((c) => `@${c}`).join("\n");
      } catch (e) { /* keep whatever is in the box */ }
    });
    document.getElementById("monitor-close").addEventListener("click", close);
    panel.addEventListener("click", (e) => { if (e.target === panel) close(); });
    saveBtn.addEventListener("click", async () => {
      saveBtn.disabled = true;
      try { await save(); saveBtn.textContent = "Saved ✓"; } catch (e) { saveBtn.textContent = "Save failed"; }
      setTimeout(() => { saveBtn.textContent = "Save list"; saveBtn.disabled = false; }, 1500);
    });
    document.getElementById("monitor-go").addEventListener("click", async () => {
      if (isGenerating) return;
      try { await save(); } catch (e) { /* scan with the saved list */ }
      close();
      sendPrompt("Content-Monitor: what did my creators post in the last 3 days?",
        "Reading your creators' posts (about 1 minute)...", "content_monitor");
    });
  }

  function setupHistoryDrawer() {
    historyBtn.addEventListener("click", () => {
      historyDrawer.classList.remove("hidden");
      drawerBackdrop.classList.remove("hidden");
      loadSessions(closeDrawer).then(addScheduledSection);
    });

    function closeDrawer() {
      historyDrawer.classList.add("hidden");
      drawerBackdrop.classList.add("hidden");
    }

    closeHistoryBtn.addEventListener("click", closeDrawer);
    drawerBackdrop.addEventListener("click", closeDrawer);
  }

  // Top of the History drawer: upcoming scheduled posts (with Cancel) and the last few that ran.
  const SCHEDULE_STATUS = { scheduled: "Scheduled", posting: "Posting now", posted: "Posted", partial: "Partly posted",
    failed: "Failed", missed: "Missed", cancelled: "Cancelled" };
  async function addScheduledSection() {
    let items = [];
    try {
      items = (await (await fetch("/api/v1/schedule")).json()).items || [];
    } catch (e) { return; }
    items = items.filter((e) => e.status !== "cancelled").slice(0, 8);
    if (!items.length) return;
    const sec = document.createElement("div");
    sec.className = "scheduled-section";
    sec.innerHTML = `<div class="digest-section-title">Scheduled posts</div>`;
    items.forEach((e) => {
      const row = document.createElement("div");
      row.className = "history-item-row scheduled-row";
      row.innerHTML = `<span class="hist-title"></span><span class="hist-meta"></span>`;
      row.querySelector(".hist-title").textContent = `${e.kind === "video" ? "🎬" : "🖼"} ${e.title}`;
      row.querySelector(".hist-meta").textContent =
        `${SCHEDULE_STATUS[e.status] || e.status} · ${fmtWhen(e.run_at_ts)} · ${e.platforms.join(", ")}` +
        (e.error ? ` · ${e.error}` : "");
      if (e.status === "scheduled") {
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "link-btn";
        cancel.textContent = "Cancel";
        cancel.addEventListener("click", async () => {
          const r = await fetch(`/api/v1/schedule/${e.id}`, { method: "DELETE" });
          if (r.ok) row.remove();
        });
        row.appendChild(cancel);
      }
      sec.appendChild(row);
    });
    historyContent.prepend(sec);
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
      const n = data.slides.length;
      grid.className = n === 1 ? "slide-grid" : `slide-grid cols-${n === 4 || n === 2 ? n : 3}`;
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
          await copyText(captionInput.value);
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

      const scheduler = buildScheduler((st) => {
        const selected = [...picker.querySelectorAll("input:checked")].map((c) => c.value);
        if (!selected.length) { st.textContent = "Select at least one platform."; return null; }
        return { kind: "carousel", ref: data.news_id, platforms: selected, format: isSingle ? "single" : "carousel",
                 brand, caption: captionInput ? captionInput.value : null, ...musicPicker.selection() };
      });
      actions.appendChild(picker);
      actions.appendChild(publishBtn);
      actions.appendChild(scheduler.button);
      block.appendChild(musicPicker.el);
      block.appendChild(actions);
      block.appendChild(scheduler.el);
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
      dl.href = downloadUrl(data.image_url);
      dl.download = "alfred-image.png";
      dl.textContent = "Download";
      actions.appendChild(dl);
      block.appendChild(actions);
      const receipt = renderUsage(data.usage && { ...data.usage, kind: "image" });
      if (receipt) block.appendChild(receipt);
      body.appendChild(block);
    }

    // His script / idea in the chat: Image (how many?) · Video (clone / normal -> length) · Just answer it.
    if (data.action === "script_kind" && data.script) {
      const wrap = document.createElement("div");
      wrap.className = "script-steps";
      const addRow = (title) => {
        const sec = makeSection(title);
        wrap.appendChild(sec.el);
        scrollToBottom();
        return sec;
      };
      const pick = (row, btn) => {
        row.querySelectorAll(".msg-action-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
      };
      let typeSec = null;
      let lenSec = null;
      let countSec = null;
      const showImageCount = () => {
        if (countSec) return;
        countSec = addRow("How many images?");
        countSec.row.appendChild(makeCountCircles(6, (n) => {
          sendPrompt(n === 1 ? "1 image" : `${n} images`, n === 1 ? "Writing the image prompt..." : `Writing ${n} image prompts...`,
            "image_prompt", { script: data.script, image_count: n, reference: !!data.reference });
        }, (n) => (n === 1 ? "1 image" : `A set of ${n} different images`)));
      };
      const showVideoType = () => {
        if (typeSec) return;
        typeSec = addRow("Which kind of video?");
        const types = [["clone", "Avatar video · you on camera (HeyGen)"], ["normal", "Standalone video · AI clips only"]];
        types.forEach(([type, label]) => {
          typeSec.row.appendChild(makeButton(label, false, (b) => {
            pick(typeSec.row, b);
            if (lenSec) lenSec.el.remove();
            lenSec = addRow("Video length");
            const lengths = type === "clone" && !data.reference
              ? [[0, `Keep my words · ~${data.keep_seconds} s`], ...data.options.map((sec) => [sec, `${sec} s`])]
              : data.options.map((sec) => [sec, `${sec} s`]);
            lengths.forEach(([secs, lbl]) => {
              lenSec.row.appendChild(makeButton(lbl, false, (lb) => {
                if (isGenerating) return;
                pick(lenSec.row, lb);
                const name = type === "clone" ? "Clone video" : "Normal video";
                sendPrompt(`${name} · ${secs ? `${secs} s` : "my words as written"}`,
                  `Planning the clips for your ${name.toLowerCase()}...`, "custom_video",
                  { script: data.script, video_type: type, video_seconds: secs, reference: !!data.reference });
              }));
            });
          }));
        });
      };
      if (data.kind === "image") showImageCount();
      else if (data.kind === "video") showVideoType();
      else {
        const kind = addRow("What should I make?");
        kind.row.appendChild(makeButton("🖼 Image", false, (btn) => { pick(kind.row, btn); showImageCount(); }));
        kind.row.appendChild(makeButton("🎬 Video", false, (btn) => { pick(kind.row, btn); showVideoType(); }));
        kind.row.appendChild(makeButton("💬 Just answer it", false, (btn) => {
          if (isGenerating) return;
          pick(kind.row, btn);
          sendPrompt("Just answer it", "Thinking...", "chat_only", { script: data.script });
        }));
      }
      body.appendChild(wrap);
    }

    // Image -> N: the exact prompts + ratio the image model will get, editable before generating.
    if (data.action === "image_prompt" && (data.prompts || data.prompt)) {
      const prompts = data.prompts && data.prompts.length ? data.prompts : [data.prompt];
      const wrap = document.createElement("div");
      wrap.className = "image-prompt-edit";
      const areas = prompts.map((p, i) => {
        if (prompts.length > 1) {
          const lbl = document.createElement("div");
          lbl.className = "format-choice-title prompt-label";
          lbl.textContent = `Image ${i + 1}`;
          wrap.appendChild(lbl);
        }
        const area = document.createElement("textarea");
        area.className = "caption-box caption-edit";
        area.value = p;
        area.rows = 2;
        const fit = () => { area.style.height = "auto"; area.style.height = `${area.scrollHeight + 2}px`; };
        area.addEventListener("input", fit);
        requestAnimationFrame(fit);  // after it is on the page, so the height is known
        wrap.appendChild(area);
        return area;
      });
      const row = document.createElement("div");
      row.className = "msg-actions";
      const ratio = document.createElement("select");
      ratio.className = "schedule-input";
      [["4:5", "4:5 post"], ["1:1", "1:1 square"], ["9:16", "9:16 story"], ["16:9", "16:9 wide"]].forEach(([v, l]) => {
        ratio.insertAdjacentHTML("beforeend", `<option value="${v}"${v === data.aspect ? " selected" : ""}>${l}</option>`);
      });
      row.appendChild(ratio);
      const label = prompts.length > 1 ? `Generate ${prompts.length} images` : "Generate image";
      row.appendChild(makeButton(label, true, () => {
        const list = areas.map((a) => a.value.trim()).filter(Boolean);
        if (!list.length || isGenerating) return;
        if (list.length === 1) {
          sendPrompt("Generate this image", "Creating your image...", "custom_image", { script: list[0], aspect: ratio.value });
        } else {
          sendPrompt(`Generate these ${list.length} images`, `Creating your ${list.length} images...`, "custom_image",
            { script: list[0], prompts: list, aspect: ratio.value });
        }
      }));
      wrap.appendChild(row);
      body.appendChild(wrap);
    }

    // A set of images from his prompts: grid, tap to view, Modify and Download on each.
    if (data.action === "images" && Array.isArray(data.image_urls) && data.image_urls.length) {
      const block = document.createElement("div");
      block.className = "carousel-block";
      const grid = document.createElement("div");
      grid.className = `slide-grid cols-${Math.min(data.image_urls.length === 4 ? 4 : 3, data.image_urls.length)}`;
      const urls = [...data.image_urls];
      urls.forEach((url, i) => {
        const cell = document.createElement("div");
        cell.className = "slide-cell";
        const link = document.createElement("a");
        link.href = url;
        link.addEventListener("click", (e) => { e.preventDefault(); openLightbox(urls, i); });
        const img = document.createElement("img");
        img.src = url;
        img.alt = `Image ${i + 1}`;
        img.addEventListener("load", scrollToBottom);
        link.appendChild(img);
        cell.appendChild(link);
        addModifyControl(cell, img, link, { kind: "studio", file: url.split("/").pop().split("?")[0] },
          (u) => { urls[i] = u; });
        const dl = document.createElement("a");
        dl.className = "msg-action-btn";
        dl.href = downloadUrl(url);
        dl.download = `alfred-image-${i + 1}.png`;
        dl.textContent = "⬇ Download";
        const actions = document.createElement("div");
        actions.className = "cell-actions";
        const modify = cell.querySelector(".modify-btn");
        cell.insertBefore(actions, modify);
        actions.appendChild(modify);
        actions.appendChild(dl);
        grid.appendChild(cell);
      });
      block.appendChild(grid);
      const receipt = renderUsage(data.usage && { ...data.usage, kind: "image set" });
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

    if (data.action === "news_source") {
      const row = document.createElement("div");
      row.className = "option-row";
      Object.entries(NEWS_SOURCES).forEach(([key, src]) => {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "option-card";
        card.innerHTML = `<span class="option-title"></span><span class="option-sub"></span>`;
        card.querySelector(".option-title").textContent = src.label;
        card.querySelector(".option-sub").textContent = src.sub;
        card.addEventListener("click", () => {
          if (isGenerating) return;
          card.classList.add("active");
          row.querySelectorAll("button").forEach((b) => { b.disabled = true; });
          aiUpdatesBtn.disabled = true;
          sendPrompt(src.prompt, src.wait, "news_scan", { news_source: key }).finally(() => (aiUpdatesBtn.disabled = false));
        });
        row.appendChild(card);
      });
      body.appendChild(row);
    }

    if (data.action === "monitor_brief" && data.count) {
      const sec = makeSection("Make my version");
      for (let k = 1; k <= data.count; k++) {
        sec.row.appendChild(makeButton(`#${k}`, false, () => {
          if (!isGenerating) sendPrompt(`Make my version of #${k}`, "Planning your version...", "monitor_video", { monitor_pick: k });
        }));
      }
      sec.row.classList.add("num-row");
      body.appendChild(sec.el);
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
      // Images section: each story asks how many images (1 = Instagram/LinkedIn single post, 2-6 = carousel)
      const imgSection = makeSection("Images");
      const choice = document.createElement("div");
      choice.className = "format-choice hidden";
      data.items.forEach((item, i) => {
        imgSection.row.appendChild(makeButton(`Img#${i + 1}`, false, (btn) => {
          imgSection.row.querySelectorAll(".msg-action-btn").forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          choice.innerHTML = `<div class="format-choice-title">#${i + 1} — how many images?</div>`;
          const opts = document.createElement("div");
          opts.className = "count-row";
          // 1 image first asks where it will be posted: Instagram is branded, LinkedIn is content only
          const brandRow = document.createElement("div");
          brandRow.className = "msg-actions hidden-row";
          brandRow.innerHTML = `<div class="format-choice-title brand-title">1 image — is this for Instagram or LinkedIn?</div>`;
          const single = (brand) => () => {
            if (!isGenerating) sendPrompt(`Make 1 ${brand === "linkedin" ? "LinkedIn" : "Instagram"} image for story #${i + 1}`,
              "Making your image...", "news_images", { story: i + 1, image_count: 1, brand });
          };
          brandRow.appendChild(makeButton("Instagram · @ashok_a.i", false, single("instagram")));
          brandRow.appendChild(makeButton("LinkedIn · Ashokachari P", false, single("linkedin")));

          // Round 1-6 circles: the tapped one fills in.
          for (let n = 1; n <= 6; n++) {
            const c = document.createElement("button");
            c.type = "button";
            c.className = "count-circle";
            c.textContent = String(n);
            c.title = n === 1 ? "1 image (Instagram or LinkedIn)" : `${n}-image Instagram carousel`;
            c.addEventListener("click", () => {
              if (isGenerating) return;
              opts.querySelectorAll(".count-circle").forEach((x) => x.classList.toggle("active", x === c));
              if (n === 1) {
                brandRow.classList.remove("hidden-row");
                scrollToBottom();
                return;
              }
              brandRow.classList.add("hidden-row");
              sendPrompt(`Make ${n} images for story #${i + 1}`, `Making ${n} images...`, "news_images",
                { story: i + 1, image_count: n });
            });
            opts.appendChild(c);
          }
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
  function renderUsage(usage) {
    if (!usage || !usage.items) return null;
    const rupees = (v) => `₹${Number(v || 0).toFixed(v > 0 && v < 10 ? 2 : 0)}`;
    const el = document.createElement("div");
    el.className = "usage-line";
    const total = usage.total_inr != null ? usage.total_inr : null;
    if (!usage.items.length || !(usage.total_usd > 0)) {
      el.textContent = "Used ₹0 · no paid service";
      return el;
    }
    // Older results were saved in USD only: show them as they were.
    el.innerHTML = `Used <b>${total != null ? rupees(total) : `$${usage.total_usd}`}</b>`;
    el.title = usage.items.map((it) => `${it.service} · ${it.item}: ${it.inr != null ? rupees(it.inr) : `$${it.usd}`}`).join("\n");
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

  // Download link that also works inside the Android app: the server sends it as an attachment.
  const downloadUrl = (url) => url + (url.includes("?") ? "&" : "?") + "download=1";

  // Copy that works on http:// too (the Android WebView blocks navigator.clipboard there).
  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
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
    const toggle = makeButton("✎ Edit", false, () => {
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
        const old = form.querySelector(".usage-line");
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
  const fmtWhen = (ts) => new Date(ts * 1000).toLocaleString([], { weekday: "short", day: "numeric", month: "short",
    hour: "numeric", minute: "2-digit" });

  // "Schedule" next to Publish: pick a date + time; the server posts it then (it must be running at that time).
  // getRequest(statusEl) returns {kind, ref, platforms, caption, ...} or null after writing why into statusEl.
  function buildScheduler(getRequest) {
    const form = document.createElement("div");
    form.className = "schedule-form hidden";
    const local = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    const input = document.createElement("input");
    input.type = "datetime-local";
    input.className = "schedule-input";
    const status = document.createElement("div");
    status.className = "publish-status";
    const button = makeButton("🗓 Schedule", false, () => {
      if (form.classList.contains("hidden")) {
        const soon = new Date(Date.now() + 3600000);
        soon.setMinutes(0, 0, 0);
        input.min = local(new Date());
        if (!input.value) input.value = local(soon);
      }
      form.classList.toggle("hidden");
      scrollToBottom();
    });
    const showScheduled = (entry) => {
      status.innerHTML = `🗓 Scheduled for <b>${escapeHtml(fmtWhen(entry.run_at_ts))}</b> · ${escapeHtml(entry.platforms.join(", "))} `;
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "link-btn";
      cancel.textContent = "Cancel";
      cancel.addEventListener("click", async () => {
        const r = await fetch(`/api/v1/schedule/${entry.id}`, { method: "DELETE" });
        status.textContent = r.ok ? "Schedule cancelled." : `Couldn't cancel: ${((await r.json()).detail) || r.status}`;
      });
      status.appendChild(cancel);
    };
    const row = document.createElement("div");
    row.className = "msg-actions";
    row.appendChild(input);
    row.appendChild(makeButton("Confirm schedule", true, async (btn) => {
      const req = getRequest(status);
      if (!req) return;
      if (!input.value) { status.textContent = "Pick a date and time."; return; }
      btn.disabled = true;
      try {
        const res = await fetch("/api/v1/schedule", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...req, run_at: new Date(input.value).toISOString() }),
        });
        const out = await res.json();
        if (!res.ok) throw new Error(out.detail || res.status);
        form.classList.add("hidden");
        showScheduled(out);
      } catch (e) {
        status.textContent = `Couldn't schedule: ${e.message}`;
      } finally {
        btn.disabled = false;
      }
    }));
    form.appendChild(row);
    form.insertAdjacentHTML("beforeend",
      `<div class="schedule-note">Alfred posts it at that time, so keep your PC and Alfred's server running.</div>`);
    const el = document.createElement("div");
    el.appendChild(form);
    el.appendChild(status);
    return { button, el };
  }

  // Before Render: every clip editable. Clone: what he says + what the top half shows. Normal: what's shown,
  // the motion, the text on screen and the clip length. The times update while he types.
  function buildPlanEditor(jobId, onSaved) {
    const el = document.createElement("div");
    el.className = "plan-editor";
    el.textContent = "Loading the clips…";
    const fmt = (t) => `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
    fetch(`/api/v1/studio/jobs/${jobId}/plan`).then((r) => r.json()).then(({ plan }) => {
      if (!plan) { el.textContent = "Couldn't load the plan."; return; }
      el.textContent = "";
      const normal = plan.type === "normal";
      const scenes = plan.scenes.map((s) => ({ ...s }));
      const durOf = (s) => (normal ? Math.max(2, Math.min(15, Math.round(+s.seconds || 5)))
        : Math.max(2, (s.spoken || "").trim().split(/\s+/).filter(Boolean).length / WORDS_PER_SECOND));
      const list = document.createElement("div");
      const totalLine = document.createElement("div");
      totalLine.className = "plan-total";
      const refreshTimes = () => {
        let t = 0;
        list.querySelectorAll(".plan-clip").forEach((card, i) => {
          const d = durOf(scenes[i]);
          card.querySelector(".plan-clip-time").textContent = `Clip ${i + 1} · ${fmt(t)}-${fmt(t + d)}`;
          t += d;
        });
        totalLine.textContent = `Total about ${Math.round(t)} s${normal ? "" : " (final timings follow your real voice)"}`;
      };
      const field = (label, value, rows, onInput) => {
        const wrap = document.createElement("label");
        wrap.className = "plan-field";
        wrap.innerHTML = `<span>${escapeHtml(label)}</span>`;
        const area = document.createElement("textarea");
        area.rows = rows;
        area.value = value || "";
        area.addEventListener("input", () => { onInput(area.value); refreshTimes(); });
        wrap.appendChild(area);
        return wrap;
      };
      const render = () => {
        list.innerHTML = "";
        scenes.forEach((s, i) => {
          const card = document.createElement("div");
          card.className = "plan-clip";
          const head = document.createElement("div");
          head.className = "plan-clip-head";
          head.innerHTML = `<span class="plan-clip-time"></span>`;
          if (scenes.length > 1) {
            const del = document.createElement("button");
            del.type = "button";
            del.className = "link-btn";
            del.textContent = "Remove";
            del.addEventListener("click", () => { scenes.splice(i, 1); render(); });
            head.appendChild(del);
          }
          card.appendChild(head);
          if (!normal) card.appendChild(field("Says", s.spoken, 2, (v) => { s.spoken = v; }));
          card.appendChild(field(normal ? "Shows (image prompt)" : "Top half shows (image prompt)", s.visual, 3,
            (v) => { s.visual = v; }));
          card.appendChild(field("Motion", s.motion, 2, (v) => { s.motion = v; }));
          if (normal) {
            card.appendChild(field("Text on screen", s.caption, 1, (v) => { s.caption = v; }));
            const len = document.createElement("label");
            len.className = "plan-field plan-seconds";
            len.innerHTML = `<span>Clip length</span>`;
            const sel = document.createElement("select");
            sel.className = "schedule-input";
            for (let k = 2; k <= 15; k++) {
              sel.insertAdjacentHTML("beforeend", `<option value="${k}"${k === durOf(s) ? " selected" : ""}>${k} s</option>`);
            }
            sel.addEventListener("change", () => { s.seconds = +sel.value; refreshTimes(); });
            len.appendChild(sel);
            card.appendChild(len);
          }
          list.appendChild(card);
        });
        refreshTimes();
      };
      const status = document.createElement("div");
      status.className = "publish-status";
      const actions = document.createElement("div");
      actions.className = "msg-actions";
      actions.appendChild(makeButton("+ Add clip", false, () => {
        scenes.push(normal ? { caption: "", visual: "", motion: "", seconds: 5 } : { spoken: "", visual: "", motion: "" });
        render();
      }));
      actions.appendChild(makeButton("Save changes", true, async (btn) => {
        btn.disabled = true;
        status.textContent = "";
        try {
          const res = await fetch(`/api/v1/studio/jobs/${jobId}/plan`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scenes }),
          });
          const out = await res.json();
          if (!res.ok) throw new Error(out.detail || res.status);
          onSaved(out);
          el.remove();
        } catch (e) {
          status.textContent = `Couldn't save: ${e.message}`;
          btn.disabled = false;
        }
      }));
      actions.appendChild(makeButton("Close", false, () => el.remove()));
      render();
      el.appendChild(list);
      el.appendChild(totalLine);
      el.appendChild(actions);
      el.appendChild(status);
      scrollToBottom();
    }).catch(() => { el.textContent = "Couldn't load the plan."; });
    return el;
  }

  // Clone videos: hear the cloned voice before paying for the video. Each clip can be redone on its own;
  // Only a preview: on Render HeyGen speaks the script itself in this voice (lips in sync). Optional.
  function buildVoicePreview(jobId, voice, onState, getVoiceId = () => null) {
    const el = document.createElement("div");
    el.className = "voice-preview";
    const status = document.createElement("div");
    status.className = "publish-status";
    const render = (v) => {
      el.innerHTML = "";
      if (!v) {
        el.innerHTML = `<div class="format-choice-title">Want to hear the voice first? (optional)</div>`;
        const row = document.createElement("div");
        row.className = "msg-actions";
        row.appendChild(makeButton("🎙 Make voice preview", true, (btn) => run(null, btn)));
        el.appendChild(row);
        el.appendChild(status);
        onState(false, false);
        return;
      }
      el.innerHTML = `<div class="format-choice-title">Your voice · ${v.duration} s · preview only: HeyGen speaks it fresh on Render, so the lips match</div>`;
      const full = document.createElement("audio");
      full.controls = true;
      full.preload = "none";
      full.src = v.url;
      full.className = "voice-audio";
      el.appendChild(full);
      v.clips.forEach((c, i) => {
        const row = document.createElement("div");
        row.className = "voice-clip";
        row.innerHTML = `<div class="voice-clip-text"><b>Clip ${i + 1}</b> · ${escapeHtml(c.text)}</div>`;
        const a = document.createElement("audio");
        a.controls = true;
        a.preload = "none";
        a.src = c.url;
        a.className = "voice-audio small";
        row.appendChild(a);
        row.appendChild(makeButton("↻ Redo", false, (btn) => run(i + 1, btn)));
        el.appendChild(row);
      });
      const row = document.createElement("div");
      row.className = "msg-actions";
      row.appendChild(makeButton("↻ Redo all", false, (btn) => run(null, btn)));
      el.appendChild(row);
      el.appendChild(status);
      onState(true, false);
    };
    const run = async (clip, btn) => {
      btn.disabled = true;
      btn.textContent = clip ? "Redoing…" : "Making your voice…";
      status.textContent = "";
      try {
        const q = new URLSearchParams();
        if (clip) q.set("clip", clip);
        if (getVoiceId()) q.set("voice_id", getVoiceId());
        const res = await fetch(`/api/v1/studio/jobs/${jobId}/voice?${q}`, { method: "POST" });
        const out = await res.json();
        if (!res.ok) throw new Error(out.detail || res.status);
        render(out.voice);
        status.textContent = clip ? `Clip ${clip} redone. Play it to check.` : "";
        onState(true, false, out.estimate);
      } catch (e) {
        btn.disabled = false;
        btn.textContent = clip ? "↻ Redo" : "🎙 Make voice preview";
        status.textContent = `Voice preview failed: ${e.message}. You can still render the old way (HeyGen makes the voice).`;
        onState(false, true);
      }
    };
    render(voice);
    return { el, reset: () => render(null) };
  }

  function estimateLine(est) {
    if (est.total_inr != null) {
      if (est.type === "normal") return `Ready to render · about ₹${est.total_inr} (images + video clips, no HeyGen)`;
      return `Ready to render · about ₹${est.total_inr} (HeyGen ₹${est.heygen_inr} + top half ₹${est.content_inr})`;
    }
    if (est.type === "normal") return `Ready to render · about $${est.total_usd ?? "?"} (images + video clips, no HeyGen)`;
    return `Ready to render · about $${est.total_usd ?? "?"} (HeyGen $${est.heygen_usd ?? "?"} + top half $${est.content_usd ?? "?"})`;
  }

  function buildMusicPicker(sources = Object.keys(MUSIC_TABS)) {
    const el = document.createElement("div");
    el.className = "music-picker";
    el.innerHTML = `<div class="msg-actions"><button type="button" class="msg-action-btn music-toggle">🎵 Background music</button>
        <span class="music-chosen"></span></div>
      <div class="music-panel hidden">
        <div class="music-tabs">${sources.map((k) =>
          `<button type="button" data-src="${k}">${MUSIC_TABS[k].label}</button>`).join("")}</div>
        <div class="music-note"></div>
        <div class="music-list"></div>
      </div>`;
    const panel = el.querySelector(".music-panel");
    const list = el.querySelector(".music-list");
    const chosen = el.querySelector(".music-chosen");
    const picks = { instagram: null, royalty_free: null };
    const cache = {};
    let tab = sources[0];
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

  // His HeyGen voices, shown under the avatars before Render: tap to pick, ▶ to hear HeyGen's sample.
  function buildVoicePicker(onChange, initialId = null) {
    const el = document.createElement("div");
    el.className = "avatar-picker voice-picker";
    el.innerHTML = `<div class="avatar-picker-label">Loading your voices…</div><div class="voice-strip"></div>`;
    const label = el.querySelector(".avatar-picker-label");
    const strip = el.querySelector(".voice-strip");
    let selected = null;
    const player = new Audio();
    fetch("/api/v1/studio/voices").then((r) => r.json()).then(({ voices = [] }) => {
      if (!voices.length) { el.remove(); return; }
      const current = voices.find((v) => v.id === initialId) || voices.find((v) => v.current) || voices[0];
      selected = current.id;
      label.textContent = `Voice: ${current.name}`;
      [current, ...voices.filter((v) => v !== current)].forEach((v) => {
        const chip = document.createElement("div");
        chip.className = "voice-chip" + (v.id === selected ? " selected" : "");
        chip.title = v.name;
        chip.innerHTML = `<span class="voice-name"></span>`;
        chip.querySelector(".voice-name").textContent = v.name;
        if (v.preview) {
          const play = document.createElement("button");
          play.type = "button";
          play.className = "music-play";
          play.textContent = "▶";
          play.setAttribute("aria-label", `Hear ${v.name}`);
          play.addEventListener("click", (e) => {
            e.stopPropagation();
            const playing = play.classList.contains("playing");
            player.pause();
            strip.querySelectorAll(".music-play.playing").forEach((b) => { b.classList.remove("playing"); b.textContent = "▶"; });
            if (playing) return;
            player.src = v.preview;
            player.play().catch(() => { play.textContent = "✕"; });
            player.onended = () => { play.classList.remove("playing"); play.textContent = "▶"; };
            play.classList.add("playing");
            play.textContent = "❚❚";
          });
          chip.prepend(play);
        }
        chip.addEventListener("click", () => {
          if (selected === v.id) return;
          selected = v.id;
          strip.querySelectorAll(".voice-chip").forEach((c) => c.classList.toggle("selected", c === chip));
          label.textContent = `Voice: ${v.name}`;
          onChange(v.id);
        });
        strip.appendChild(chip);
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
    const scheduler = buildScheduler((st) => {
      const selected = [...picker.querySelectorAll("input:checked")].map((c) => c.value);
      if (!selected.length) { st.textContent = "Select where to post it."; return null; }
      return { kind: "video", ref: jobId, platforms: selected, caption: caption.value };
    });
    actions.appendChild(scheduler.button);
    actions.appendChild(makeButton("Copy caption", false, async (btn) => {
      await copyText(caption.value);
      btn.textContent = "Copied";
    }));
    wrap.appendChild(actions);
    wrap.appendChild(scheduler.el);
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
            <div class="msg-actions"><a class="msg-action-btn" href="${downloadUrl(job.url)}" download="alfred-video.mp4">Download video</a></div>`;
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
          // Nothing paid runs until he confirms. He can edit the clips first.
          statusMsg().textContent = estimateLine(job.estimate || {});
          if (!box.querySelector(".video-job-actions")) {
            const normal = job.video_type === "normal";
            const avatars = normal ? null : buildAvatarPicker();
            let voicePanel = null;
            // Another voice picked: the old preview no longer matches, so he hears the new one first.
            const voices = normal ? null : buildVoicePicker(() => { if (voicePanel) voicePanel.reset(); }, job.voice_id);
            const musicPick = normal ? buildMusicPicker(["royalty_free"]) : null;
            if (avatars) box.appendChild(avatars.el);
            if (voices) box.appendChild(voices.el);
            if (musicPick) box.appendChild(musicPick.el);
            const row = document.createElement("div");
            row.className = "msg-actions video-job-actions";
            const cleanup = () => {
              row.remove();
              if (avatars) avatars.el.remove();
              if (voices) voices.el.remove();
              if (musicPick) musicPick.el.remove();
              if (voicePanel) voicePanel.el.remove();
              box.querySelectorAll(".plan-editor").forEach((e) => e.remove());
            };
            const go = makeButton("Render video", true, async (btn) => {
              btn.disabled = true;
              btn.textContent = "Starting...";
              await fetch(`/api/v1/studio/jobs/${jobId}/approve`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ avatar_id: avatars ? avatars.selected() : null,
                                       voice_id: voices ? voices.selected() : null,
                                       music_id: musicPick ? musicPick.selection().music_id : null }),
              });
              cleanup();
              tick();
            });
            const edit = makeButton("✎ Edit clips", false, () => {
              if (box.querySelector(".plan-editor")) return;
              row.before(buildPlanEditor(jobId, (out) => {
                const text = box.closest(".bubble-body") && box.closest(".bubble-body").querySelector(".bubble-text");
                if (text) {
                  text.innerHTML = formatMarkdown("Here's the plan for your video (edited):\n\n" + out.markdown +
                    "\n\nNothing has been rendered yet. Tap Render when you're happy with it.");
                }
                statusMsg().textContent = estimateLine(out.estimate || {});
                if (voicePanel && !out.voice) voicePanel.reset();  // the words changed: hear the new voice
              }));
            });
            const cancel = makeButton("Cancel", false, async () => {
              await fetch(`/api/v1/studio/jobs/${jobId}/cancel`, { method: "POST" });
              cleanup();
              tick();
            });
            row.appendChild(go);
            row.appendChild(edit);
            row.appendChild(cancel);
            if (!normal) {  // the voice preview is optional: Render is always available
              voicePanel = buildVoicePreview(jobId, job.voice, (ready, failed, est) => {
                go.disabled = false;
                go.textContent = "Render video";
                if (est) statusMsg().textContent = estimateLine(est);
              }, () => (voices ? voices.selected() : null));
              box.appendChild(voicePanel.el);
            }
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

  // Round 1..max circles; the tapped one fills in. onPick(n) runs on tap.
  function makeCountCircles(max, onPick, titleFor) {
    const row = document.createElement("div");
    row.className = "count-row";
    for (let n = 1; n <= max; n++) {
      const c = document.createElement("button");
      c.type = "button";
      c.className = "count-circle";
      c.textContent = String(n);
      if (titleFor) c.title = titleFor(n);
      c.addEventListener("click", () => {
        if (isGenerating) return;
        row.querySelectorAll(".count-circle").forEach((x) => x.classList.toggle("active", x === c));
        onPick(n);
      });
      row.appendChild(c);
    }
    return row;
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
      // [text](https://...) links, opened in a new tab
      .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)"]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      // "::meta ..." = small coloured info line (impact, category, source) at the end of a news story
      // (a single line break after it is dropped: the line is already a block, so <br> would add a gap)
      .replace(/^::meta (.+)\n(?!\n)/gm, '<span class="news-meta">$1</span>')
      .replace(/^::meta (.+)$/gm, '<span class="news-meta">$1</span>')
      .replace(/^#{1,4}\s+(.+)$/gm, "<strong>$1</strong>")
      .replace(/^\s*[*-]\s+/gm, "• ")
      // Bold only for headings / titles / sub-headings: a whole bold line (optionally followed by [..]),
      // or a bold "Label:" at the start of a line. Bold words inside sentences become plain text.
      // Video plan title: "**Title** (9:16 split screen, about 38s)" -> bold title + muted details
      .replace(/^\*\*([^*\n]+)\*\* \((9:16[^)\n]*)\)$/gm, '<strong>$1</strong> <span class="plan-muted">$2</span>')
      .replace(/^(\W{0,3}\s*)\*\*([^*\n]+?)\*\*(\s*(\[[^\]\n]*\])?\s*)$/gm, "$1<strong>$2</strong>$3")
      .replace(/^(\W{0,3}\s*)\*\*([^*\n]{1,40}:)\*\*/gm, "$1<strong>$2</strong>")
      .replace(/\*\*(.*?)\*\*/g, "$1")
      // Video plan colours: clip heading (accent) + time (muted), labels (soft blue), hook label (accent)
      .replace(/^<strong>(Clip \d+) \(([^)\n]+)\)<\/strong>/gm, '<strong class="md-accent">$1</strong> <span class="plan-muted">$2</span>')
      .replace(/^(Says|Top shows|Shows|Text on screen|Hook|Why it worked|Your angle):/gm, '<span class="plan-label">$1:</span>')
      .replace(/^<strong>Hook:<\/strong>/gm, '<strong class="md-accent">Hook:</strong>')
      .replace(/^((?:Top half|No avatar): [^\n]+)$/gm, '<span class="plan-muted">$1</span>')
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
