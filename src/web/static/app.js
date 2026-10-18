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
    contentMonitorBtn.addEventListener("click", () => {
      contentMonitorBtn.classList.add("pulse");
      setTimeout(() => contentMonitorBtn.classList.remove("pulse"), 600);
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

  async function sendPrompt(text, thinkingLabel, mode = null) {
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
        body.appendChild(list);
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
      data.slides.forEach((url, i) => {
        const link = document.createElement("a");
        link.href = url;
        link.addEventListener("click", (e) => {
          e.preventDefault();
          openLightbox(data.slides, i);
        });
        const img = document.createElement("img");
        img.src = url;
        img.alt = `Slide ${i + 1}`;
        img.addEventListener("load", scrollToBottom);
        link.appendChild(img);
        grid.appendChild(link);
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
            body: JSON.stringify({ platforms: selected, format: isSingle ? "single" : "carousel", brand }),
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
      block.appendChild(actions);
      block.appendChild(status);
      body.appendChild(block);
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
