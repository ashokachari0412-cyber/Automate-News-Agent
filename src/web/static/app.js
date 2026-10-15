/**
 * ALFRED // Wayne Tech Executive AI Intelligence & Content Studio
 * Client Application Logic
 */

(function () {
  // State
  let allNewsItems = [];
  let filteredNewsItems = [];
  let activeNews = null;
  let currentSlideNum = 1;
  let activeCategory = "ALL";

  // Elements
  const feedList = document.getElementById("radar-feed-list");
  const newsCountBadge = document.getElementById("news-count-badge");
  const newsSearch = document.getElementById("news-search");
  const categoryFilters = document.getElementById("category-filters");
  const scanBtn = document.getElementById("scan-btn");
  const liveClock = document.getElementById("live-clock");

  // Studio Elements
  const activeStoryTitle = document.getElementById("active-story-title");
  const slideTabs = document.getElementById("slide-nav-tabs");
  const activeSlideImg = document.getElementById("active-slide-img");
  const slideLoader = document.getElementById("slide-loader");
  const slideLoaderText = document.getElementById("slide-loader-text");
  const currentSlideBadge = document.getElementById("current-slide-badge");
  const slideTypeText = document.getElementById("slide-type-text");
  const slideContextText = document.getElementById("slide-context-text");
  const prevSlideBtn = document.getElementById("prev-slide-btn");
  const nextSlideBtn = document.getElementById("next-slide-btn");
  const slideDots = document.getElementById("slide-dots");
  const regenCarouselBtn = document.getElementById("regen-carousel-btn");
  const captionPreview = document.getElementById("caption-preview");
  const copyCaptionBtn = document.getElementById("copy-caption-btn");
  const publishBtn = document.getElementById("publish-btn");

  // Alfred Elements
  const alfredChatBox = document.getElementById("alfred-chat-box");
  const alfredUserInput = document.getElementById("alfred-user-input");
  const alfredSendBtn = document.getElementById("alfred-send-btn");
  const chatContextLabel = document.getElementById("chat-context-label");
  const alfredChips = document.getElementById("alfred-chips");

  // Toast
  const toast = document.getElementById("toast");
  const toastMsg = document.getElementById("toast-msg");
  const toastIcon = document.getElementById("toast-icon");
  let toastTimer = null;

  // Initialize
  function init() {
    startClock();
    setupEventListeners();
    fetchNews();
  }

  function startClock() {
    function update() {
      const now = new Date();
      liveClock.textContent = now.toLocaleTimeString("en-US", { hour12: false }) + " UTC";
    }
    update();
    setInterval(update, 1000);
  }

  function showToast(message, icon = "⚡", duration = 3500) {
    if (toastTimer) clearTimeout(toastTimer);
    toastIcon.textContent = icon;
    toastMsg.textContent = message;
    toast.classList.remove("hidden");
    toastTimer = setTimeout(() => {
      toast.classList.add("hidden");
    }, duration);
  }

  // ============================================================================
  // NEWS FEED LOGIC
  // ============================================================================

  async function fetchNews() {
    try {
      const res = await fetch("/api/v1/news");
      if (!res.ok) throw new Error("Failed to load intelligence feed");
      const data = await res.json();
      allNewsItems = data.items || [];
      applyFilters();
      if (allNewsItems.length > 0 && !activeNews) {
        selectStory(allNewsItems[0]);
      }
    } catch (err) {
      console.error("Fetch news error:", err);
      feedList.innerHTML = `
        <div class="loading-state">
          <p style="color: var(--accent-red)">⚠️ Neural feed offline. Reconnecting...</p>
          <button class="btn btn-secondary btn-sm" onclick="location.reload()">Retry Link</button>
        </div>`;
    }
  }

  function applyFilters() {
    const query = (newsSearch.value || "").toLowerCase().trim();
    filteredNewsItems = allNewsItems.filter((item) => {
      const matchesCat =
        activeCategory === "ALL" ||
        (item.category && item.category.toLowerCase().includes(activeCategory.toLowerCase()));
      const matchesSearch =
        !query ||
        item.title.toLowerCase().includes(query) ||
        (item.summary && item.summary.toLowerCase().includes(query));
      return matchesCat && matchesSearch;
    });

    newsCountBadge.textContent = `${filteredNewsItems.length} stories`;
    renderFeedList();
  }

  function renderFeedList() {
    if (filteredNewsItems.length === 0) {
      feedList.innerHTML = `
        <div class="loading-state">
          <p>No stories matching current parameters.</p>
        </div>`;
      return;
    }

    feedList.innerHTML = filteredNewsItems
      .map((item) => {
        const isActive = activeNews && activeNews.id === item.id ? "active" : "";
        const catClass = getCategoryClass(item.category);
        const score = item.scores?.total ? Math.round(item.scores.total) : 95;
        const source = item.primary_source_handle ? `@${item.primary_source_handle}` : "Authorized Feed";

        return `
          <div class="news-card ${isActive}" data-id="${item.id}">
            <div class="card-meta-row">
              <span class="cat-badge ${catClass}">${escapeHtml(item.category || "AI Update")}</span>
              <span class="score-badge">⚡ ${score} pts</span>
            </div>
            <div class="card-title">${escapeHtml(item.title)}</div>
            <div class="card-summary">${escapeHtml(item.summary || item.hook || "")}</div>
            <div class="card-footer">
              <span class="card-source">${escapeHtml(source)}</span>
              <div class="card-actions">
                <button class="card-btn load-studio-btn" data-id="${item.id}">Studio 🎨</button>
                <button class="card-btn deepdive-btn" data-id="${item.id}">Briefing 🎩</button>
              </div>
            </div>
          </div>
        `;
      })
      .join("");
  }

  function getCategoryClass(category = "") {
    const c = category.toLowerCase();
    if (c.includes("tool")) return "tool";
    if (c.includes("frontier") || c.includes("model")) return "frontier";
    if (c.includes("agent")) return "agent";
    if (c.includes("robot")) return "robotics";
    return "tool";
  }

  // ============================================================================
  // CAROUSEL STUDIO LOGIC
  // ============================================================================

  function selectStory(item) {
    if (!item) return;
    activeNews = item;
    currentSlideNum = 1;

    // Update UI highlights
    activeStoryTitle.textContent = item.title;
    chatContextLabel.textContent = `Context: ${item.title}`;

    // Re-render feed to show active border
    renderFeedList();

    // Load active slide
    loadSlide(currentSlideNum);

    // Update Caption Preview
    updateCaptionPreview(item);
  }

  function loadSlide(slideNum) {
    if (!activeNews) return;
    currentSlideNum = slideNum;

    // Update Tabs
    document.querySelectorAll(".slide-tab").forEach((tab) => {
      tab.classList.toggle("active", parseInt(tab.dataset.slide) === slideNum);
    });

    // Update Dots
    document.querySelectorAll(".slide-dots .dot").forEach((dot, idx) => {
      dot.classList.toggle("active", idx + 1 === slideNum);
    });

    currentSlideBadge.textContent = `Slide 0${slideNum} of 04`;

    // Slide Context Description
    const descriptions = [
      {
        title: "SLIDE 1 // VIRAL HOOK & BESPOKE AI HERO",
        desc: activeNews.hook || activeNews.title,
      },
      {
        title: "SLIDE 2 // SYSTEM ARCHITECTURE & BREAKDOWN",
        desc: activeNews.summary || "System architecture & engineering mechanism breakdown.",
      },
      {
        title: "SLIDE 3 // KEY STRATEGIC TAKEAWAYS",
        desc: Array.isArray(activeNews.key_bullet_points)
          ? activeNews.key_bullet_points.join(" • ")
          : "Essential operational capabilities.",
      },
      {
        title: "SLIDE 4 // STRATEGIC ACTION & CREATOR CTA",
        desc: activeNews.why_it_matters || "Why it matters for creators, builders, and developers.",
      },
    ];

    const currentDesc = descriptions[slideNum - 1] || descriptions[0];
    slideTypeText.textContent = currentDesc.title;
    slideContextText.textContent = currentDesc.desc;

    // Load Slide Image
    showSlideLoader(true, "Alfred is preparing visual slide...");
    const imgUrl = `/api/v1/carousel/${activeNews.id}/slide/${slideNum}?t=${Date.now()}`;

    const tempImg = new Image();
    tempImg.onload = () => {
      activeSlideImg.src = imgUrl;
      showSlideLoader(false);
    };
    tempImg.onerror = () => {
      // Auto trigger generation if image is not ready yet
      generateCarouselSlides(false);
    };
    tempImg.src = imgUrl;
  }

  function showSlideLoader(show, text = "") {
    if (show) {
      slideLoader.classList.remove("hidden");
      if (text) slideLoaderText.textContent = text;
    } else {
      slideLoader.classList.add("hidden");
    }
  }

  async function generateCarouselSlides(manualTrigger = true) {
    if (!activeNews) return;
    showSlideLoader(true, "Alfred is generating bespoke 8K visual artwork & typesetting slides...");

    try {
      const res = await fetch(`/api/v1/carousel/generate/${activeNews.id}`, {
        method: "POST",
      });
      if (!res.ok) throw new Error("Carousel generation returned error");
      const data = await res.json();
      showToast("4-Slide Carousel successfully rendered by Alfred", "🎨");
      loadSlide(currentSlideNum);
    } catch (err) {
      console.error("Carousel generation error:", err);
      showToast("Failed to render slides: " + err.message, "⚠️");
      showSlideLoader(false);
    }
  }

  function updateCaptionPreview(item) {
    const bullets = (item.key_bullet_points || []).map((b) => `• ${b}`).join("\n");
    const tags = `#AI #ArtificialIntelligence #TechNews #GenerativeAI #MachineLearning #TechTrends #AshokAI #WayneTech`;

    const caption = `🚨 ${item.title}\n\n${item.summary}\n\nKey Takeaways:\n${bullets}\n\n💡 Why it matters:\n${item.why_it_matters || ""}\n\nSwipe through for the full breakdown! 👉\nFollow @ashok_a.i for daily high-signal AI intelligence.\n\n${tags}`;

    captionPreview.value = caption;
  }

  async function publishCarousel() {
    if (!activeNews) return;
    const platforms = [];
    if (document.getElementById("pub-ig").checked) platforms.push("instagram");
    if (document.getElementById("pub-linkedin").checked) platforms.push("linkedin");
    if (document.getElementById("pub-x").checked) platforms.push("x");
    if (document.getElementById("pub-yt").checked) platforms.push("youtube");

    if (platforms.length === 0) {
      showToast("Please select at least one publishing platform", "⚠️");
      return;
    }

    publishBtn.disabled = true;
    publishBtn.innerHTML = `<span>Dispatching...</span>`;

    try {
      const res = await fetch(`/api/v1/carousel/publish/${activeNews.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platforms }),
      });
      const data = await res.json();
      showToast("Intelligence carousel dispatched across selected channels!", "🚀");

      // Notify in Alfred Chat
      appendChatMessage(
        "Alfred",
        `Master Ashok, I have dispatched the 4-slide carousel for <b>"${escapeHtml(
          activeNews.title
        )}"</b> to your selected networks (${platforms.join(", ")}). The transmission report is logged.`,
        "alfred"
      );
    } catch (err) {
      showToast("Publish error: " + err.message, "⚠️");
    } finally {
      publishBtn.disabled = false;
      publishBtn.innerHTML = `<span class="btn-icon">🚀</span><span>Publish Carousel</span>`;
    }
  }

  // ============================================================================
  // ALFRED EXECUTIVE AI BUTLER CHAT
  // ============================================================================

  async function sendAlfredMessage(customText = null) {
    const text = customText || alfredUserInput.value.trim();
    if (!text) return;

    // Append user message
    appendChatMessage("Master Ashok", text, "user");
    if (!customText) alfredUserInput.value = "";

    // Show Alfred Thinking
    const thinkingId = appendThinkingMessage();

    try {
      const res = await fetch("/api/v1/alfred/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          context_news_id: activeNews ? activeNews.id : null,
        }),
      });
      const data = await res.json();
      removeMessage(thinkingId);
      appendChatMessage("Alfred", data.reply, "alfred");
    } catch (err) {
      removeMessage(thinkingId);
      appendChatMessage(
        "Alfred",
        `My sincere apologies, Master Ashok. A momentary static in our neural uplink: ${err.message}`,
        "alfred"
      );
    }
  }

  async function requestDeepDive(newsId) {
    const item = allNewsItems.find((n) => n.id === newsId) || activeNews;
    if (!item) return;

    selectStory(item);

    appendChatMessage(
      "Master Ashok",
      `Alfred, I require an executive intelligence breakdown on "${item.title}".`,
      "user"
    );

    const thinkingId = appendThinkingMessage();

    try {
      const res = await fetch(`/api/v1/alfred/deepdive/${item.id}`, {
        method: "POST",
      });
      const data = await res.json();
      removeMessage(thinkingId);
      appendChatMessage("Alfred", data.briefing, "alfred");
    } catch (err) {
      removeMessage(thinkingId);
      appendChatMessage(
        "Alfred",
        `Pardon me, Sir, but our intelligence servers were unable to compile the dossier: ${err.message}`,
        "alfred"
      );
    }
  }

  function appendChatMessage(sender, content, role = "alfred") {
    const msgDiv = document.createElement("div");
    msgDiv.className = `chat-msg ${role}`;

    // Format newlines and markdown-like bold/lists
    let formatted = content
      .replace(/\n\n/g, "<br><br>")
      .replace(/\n/g, "<br>")
      .replace(/\*\*(.*?)\*\*/g, "<b>$1</b>");

    if (role === "alfred") {
      msgDiv.innerHTML = `
        <div class="msg-avatar">🎩</div>
        <div class="msg-content">
          <div class="msg-sender">Alfred Pennyworth</div>
          <div>${formatted}</div>
        </div>
      `;
    } else {
      msgDiv.innerHTML = `
        <div class="msg-content">
          <div>${formatted}</div>
        </div>
      `;
    }

    alfredChatBox.appendChild(msgDiv);
    alfredChatBox.scrollTop = alfredChatBox.scrollHeight;
  }

  function appendThinkingMessage() {
    const id = "thinking-" + Date.now();
    const msgDiv = document.createElement("div");
    msgDiv.id = id;
    msgDiv.className = "chat-msg alfred";
    msgDiv.innerHTML = `
      <div class="msg-avatar">🎩</div>
      <div class="msg-content">
        <div class="msg-sender">Alfred Pennyworth</div>
        <div style="display:flex; align-items:center; gap:8px;">
          <div class="radar-spinner" style="width:14px; height:14px;"></div>
          <span style="font-size:0.75rem; color:var(--accent-gold)">Alfred is formulating your response...</span>
        </div>
      </div>
    `;
    alfredChatBox.appendChild(msgDiv);
    alfredChatBox.scrollTop = alfredChatBox.scrollHeight;
    return id;
  }

  function removeMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
  }

  // ============================================================================
  // SCAN ORCHESTRATION
  // ============================================================================

  async function triggerFullScan() {
    scanBtn.disabled = true;
    scanBtn.innerHTML = `
      <div class="radar-spinner" style="width:14px; height:14px;"></div>
      <span class="btn-text">Scanning 18 Sources...</span>
    `;

    showToast("Alfred has initiated autonomous radar scan across 18 AI feeds...", "📡", 4000);

    try {
      const res = await fetch("/api/v1/research/scan", { method: "POST" });
      const data = await res.json();
      await fetchNews();
      showToast(data.message || "Autonomous scan complete!", "⚡", 5000);

      appendChatMessage(
        "Alfred",
        `Master Ashok, the scan across all eighteen authorized channels is complete. We have synthesized ${data.count} high-priority developments for your review.`,
        "alfred"
      );
    } catch (err) {
      showToast("Scan encountered an anomaly: " + err.message, "⚠️");
    } finally {
      scanBtn.disabled = false;
      scanBtn.innerHTML = `
        <span class="btn-icon">⚡</span>
        <span class="btn-text">Scan 18 Sources</span>
      `;
    }
  }

  // ============================================================================
  // EVENT LISTENERS
  // ============================================================================

  function setupEventListeners() {
    // Scan button
    scanBtn.addEventListener("click", triggerFullScan);

    // Search & Filter
    newsSearch.addEventListener("input", applyFilters);

    categoryFilters.addEventListener("click", (e) => {
      const chip = e.target.closest(".filter-chip");
      if (!chip) return;
      document.querySelectorAll(".filter-chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      activeCategory = chip.dataset.category;
      applyFilters();
    });

    // Feed delegation
    feedList.addEventListener("click", (e) => {
      const studioBtn = e.target.closest(".load-studio-btn");
      const deepdiveBtn = e.target.closest(".deepdive-btn");
      const card = e.target.closest(".news-card");

      if (deepdiveBtn) {
        e.stopPropagation();
        requestDeepDive(deepdiveBtn.dataset.id);
        return;
      }

      if (studioBtn) {
        e.stopPropagation();
        const item = allNewsItems.find((n) => n.id === studioBtn.dataset.id);
        if (item) selectStory(item);
        return;
      }

      if (card) {
        const item = allNewsItems.find((n) => n.id === card.dataset.id);
        if (item) selectStory(item);
      }
    });

    // Slide switcher tabs
    slideTabs.addEventListener("click", (e) => {
      const tab = e.target.closest(".slide-tab");
      if (!tab) return;
      loadSlide(parseInt(tab.dataset.slide));
    });

    // Prev / Next
    prevSlideBtn.addEventListener("click", () => {
      let next = currentSlideNum - 1;
      if (next < 1) next = 4;
      loadSlide(next);
    });

    nextSlideBtn.addEventListener("click", () => {
      let next = currentSlideNum + 1;
      if (next > 4) next = 1;
      loadSlide(next);
    });

    // Keyboard navigation
    window.addEventListener("keydown", (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
      if (e.key === "ArrowLeft") prevSlideBtn.click();
      if (e.key === "ArrowRight") nextSlideBtn.click();
    });

    // Re-render carousel
    regenCarouselBtn.addEventListener("click", () => generateCarouselSlides(true));

    // Copy caption
    copyCaptionBtn.addEventListener("click", () => {
      navigator.clipboard.writeText(captionPreview.value).then(() => {
        showToast("Caption & tags copied to clipboard!", "📋");
      });
    });

    // Publish carousel
    publishBtn.addEventListener("click", publishCarousel);

    // Alfred prompt chips
    alfredChips.addEventListener("click", (e) => {
      const chip = e.target.closest(".chip");
      if (!chip) return;
      sendAlfredMessage(chip.dataset.prompt);
    });

    // Alfred chat send
    alfredSendBtn.addEventListener("click", () => sendAlfredMessage());
    alfredUserInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendAlfredMessage();
      }
    });
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

  // Launch on DOM ready
  document.addEventListener("DOMContentLoaded", init);
})();
