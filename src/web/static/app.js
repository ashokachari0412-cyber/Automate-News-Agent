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

  // History Drawer
  const historyBtn = document.getElementById("history-btn");
  const newChatBtn = document.getElementById("new-chat-btn");
  const historyDrawer = document.getElementById("history-drawer");
  const closeHistoryBtn = document.getElementById("close-history-btn");
  const drawerBackdrop = document.getElementById("drawer-backdrop");
  const historyRows = document.querySelectorAll(".history-item-row");

  // Tools
  const attachBtn = document.getElementById("attach-btn");
  const voiceBtn = document.getElementById("voice-btn");

  let isGenerating = false;

  function init() {
    setupInputEvents();
    setupActionCards();
    setupHistoryDrawer();
    setupToolButtons();
    newChatBtn.addEventListener("click", clearMemory);
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

  async function setupGoogleButton() {
    const btn = document.getElementById("google-btn");
    const label = btn.querySelector("span");
    let connected = false;
    try {
      const s = await (await fetch("/api/v1/google/status")).json();
      connected = s.connected;
    } catch (e) {
      console.warn("Google status unavailable:", e);
    }
    label.textContent = connected ? "Google connected" : "Connect Google";
    btn.title = connected ? "Click to disconnect your Google account" : "Connect your Google account";
    btn.addEventListener("click", async () => {
      if (!connected) {
        window.location.href = "/api/v1/google/connect";
        return;
      }
      await fetch("/api/v1/google/disconnect", { method: "POST" });
      connected = false;
      label.textContent = "Connect Google";
    });
  }

  async function clearMemory() {
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
      sendBtn.disabled = this.value.trim().length === 0 || isGenerating;
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
        if (prompt) {
          sendPrompt(prompt);
        }
      });
    });
  }

  function setupHistoryDrawer() {
    historyBtn.addEventListener("click", () => {
      historyDrawer.classList.remove("hidden");
      drawerBackdrop.classList.remove("hidden");
    });

    function closeDrawer() {
      historyDrawer.classList.add("hidden");
      drawerBackdrop.classList.add("hidden");
    }

    closeHistoryBtn.addEventListener("click", closeDrawer);
    drawerBackdrop.addEventListener("click", closeDrawer);

    historyRows.forEach((row) => {
      row.addEventListener("click", () => {
        const prompt = row.dataset.prompt;
        closeDrawer();
        if (prompt) {
          sendPrompt(prompt);
        }
      });
    });
  }

  function setupToolButtons() {
    attachBtn.addEventListener("click", () => {
      sendPrompt("Alfred, summarize the current active repository context, models, and recent research findings.");
    });

    voiceBtn.addEventListener("click", () => {
      chatInput.placeholder = "Listening... (or type your request)";
      setTimeout(() => {
        chatInput.placeholder = "Type your request to Alfred...";
      }, 3000);
    });
  }

  function submitQuery() {
    const text = chatInput.value.trim();
    if (!text || isGenerating) return;

    chatInput.value = "";
    chatInput.style.height = "auto";
    sendBtn.disabled = true;

    sendPrompt(text);
  }

  async function sendPrompt(text) {
    if (heroSection) {
      heroSection.classList.add("hidden");
    }

    appendMessage("user", text);

    isGenerating = true;
    sendBtn.disabled = true;
    const isImageJob = /image|carousel|slide|photo|post/i.test(text) && !/drive|gmail|email/i.test(text);
    const thinkingId = appendThinkingBubble(
      isImageJob ? "Alfred is creating 4 unique images (about 1-2 minutes)..." : undefined
    );

    try {
      const res = await fetch("/api/v1/alfred/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });

      const data = await res.json();
      removeElement(thinkingId);
      appendMessage("assistant", data.reply || "At your service, Master Ashok.", data);
    } catch (err) {
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

  function appendMessage(role, text, data) {
    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${role}`;

    const formatted = formatMarkdown(text);

    if (role === "assistant") {
      bubble.innerHTML = `
        <div class="bubble-avatar">
          <img class="avatar-logo-img" src="logo.png" alt="Alfred">
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

      if (data.caption) {
        const cap = document.createElement("div");
        cap.className = "caption-box";
        cap.textContent = data.caption;
        block.appendChild(cap);
      }

      const actions = document.createElement("div");
      actions.className = "msg-actions";
      if (data.caption) {
        actions.appendChild(makeButton("Copy caption", false, async (btn) => {
          await navigator.clipboard.writeText(data.caption);
          btn.textContent = "Copied";
        }));
      }
      const platforms = [["instagram", "Instagram"], ["linkedin", "LinkedIn"], ["youtube", "YouTube Short"]];
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
            body: JSON.stringify({ platforms: selected }),
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
      const actions = document.createElement("div");
      actions.className = "msg-actions";
      data.items.forEach((item, i) => {
        actions.appendChild(makeButton(`Images for #${i + 1}`, false, () => {
          if (!isGenerating) sendPrompt(`generate images for ${i + 1}`);
        }));
        actions.appendChild(makeButton(`Video for #${i + 1}`, false, () => {
          if (!isGenerating) sendPrompt(`generate video for ${i + 1}`);
        }));
      });
      body.appendChild(actions);
    }
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
        <img class="avatar-logo-img" src="logo.png" alt="Alfred">
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
      .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
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
