// ---------- Поиск по всем растениям пользователя (страница /users/{id}) ----------

(function initPlantSearch() {
  const input = document.getElementById("plant-search");
  if (!input) return;

  const clearBtn = document.getElementById("plant-search-clear");
  const countEl = document.getElementById("plant-search-count");
  const groupCards = Array.from(document.querySelectorAll(".plant-group-card"));

  const normalize = (text) => text.trim().toLowerCase();

  function applyFilter() {
    const query = normalize(input.value);
    clearBtn.hidden = query.length === 0;

    if (!query) {
      groupCards.forEach((card) => {
        card.style.display = "";
        card.querySelectorAll("ul.plants li").forEach((li) => {
          li.style.display = "";
        });
      });
      countEl.textContent = "";
      return;
    }

    let totalMatches = 0;

    groupCards.forEach((card) => {
      const groupNameMatches = normalize(card.dataset.groupName || "").includes(query);
      const items = Array.from(card.querySelectorAll("ul.plants li"));
      let visibleCount = 0;

      items.forEach((li) => {
        const name = li.querySelector(".plant-name")?.textContent || "";
        const comment = li.querySelector(".plant-comment")?.textContent || "";
        const matches =
          groupNameMatches || normalize(name).includes(query) || normalize(comment).includes(query);
        li.style.display = matches ? "" : "none";
        if (matches) visibleCount += 1;
      });

      totalMatches += visibleCount;
      card.style.display = visibleCount > 0 ? "" : "none";
    });

    countEl.textContent =
      totalMatches > 0 ? `Найдено: ${totalMatches}` : "Ничего не найдено";
  }

  input.addEventListener("input", applyFilter);
  clearBtn.addEventListener("click", () => {
    input.value = "";
    applyFilter();
    input.focus();
  });
})();

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;

  const confirmText = form.dataset.confirm;
  if (confirmText && !window.confirm(confirmText)) {
    event.preventDefault();
    return;
  }

  const submitButton = form.querySelector("button[type=submit], input[type=submit]");
  if (submitButton) {
    setTimeout(() => {
      submitButton.disabled = true;
    }, 0);
  }
});
