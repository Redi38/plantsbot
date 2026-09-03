// Раньше подтверждение на каждой опасной кнопке (удалить растение/группу/
// пользователя) собиралось строкой в атрибуте onsubmit="return confirm('...')"
// прямо в шаблоне. Вынесено сюда: форма просто помечается data-confirm="текст",
// а показ диалога и блокировку повторной отправки делает этот файл — один раз
// для всех страниц админки.

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;

  const confirmText = form.dataset.confirm;
  if (confirmText && !window.confirm(confirmText)) {
    event.preventDefault();
    return;
  }

  // Разрушающие действия (delete/import и т.п.) отправляются один раз —
  // блокируем кнопку сразу после сабмита, чтобы повторный клик/двойной тап
  // на мобильном не отправил форму ещё раз, пока страница перезагружается.
  const submitButton = form.querySelector("button[type=submit], input[type=submit]");
  if (submitButton) {
    // на следующий тик, чтобы браузер успел собрать данные формы
    // до того, как кнопка станет disabled (disabled-поля не отправляются)
    setTimeout(() => {
      submitButton.disabled = true;
    }, 0);
  }
});
