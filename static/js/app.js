(() => {
  const badge = document.getElementById("connection-status");
  const offlineQueueKey = "ppOfflineSubmitQueue:v1";
  const readOfflineQueue = () => {
    try {
      return JSON.parse(localStorage.getItem(offlineQueueKey) || "[]");
    } catch (_) {
      return [];
    }
  };
  const writeOfflineQueue = (queue) => {
    localStorage.setItem(offlineQueueKey, JSON.stringify(queue));
  };
  const refreshCsrfToken = async (form) => {
    if ((form?.method || "get").toLowerCase() !== "post" || !navigator.onLine) return "";
    const response = await fetch("/sesion/csrf/", {
      credentials: "same-origin",
      cache: "no-store",
      headers: {"X-Requested-With": "XMLHttpRequest"},
    });
    if (!response.ok) throw new Error("No se pudo renovar la sesión");
    const payload = await response.json();
    const token = payload.csrfToken || "";
    form.querySelectorAll('input[name="csrfmiddlewaretoken"]').forEach((input) => {
      input.value = token;
    });
    return token;
  };
  const updateConnection = () => {
    if (!badge) return;
    const offline = !navigator.onLine;
    const pending = readOfflineQueue().length;
    badge.classList.toggle("offline", offline || pending > 0);
    if (offline) {
      badge.textContent = pending
        ? `Sin conexión · ${pending} envío(s) guardado(s), se sincronizan al volver internet`
        : "Sin conexión · los envíos de túneles se guardarán en este equipo";
    } else if (pending) {
      badge.textContent = `${pending} envío(s) pendiente(s) · sincronizando…`;
    } else {
      badge.textContent = "";
    }
  };
  const queueOfflineSubmit = (form, submitter) => {
    const formData = new FormData(form);
    if (submitter?.name) formData.append(submitter.name, submitter.value);
    const queue = readOfflineQueue();
    queue.push({
      id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      url: form.action || location.href,
      method: (form.method || "post").toUpperCase(),
      label: form.dataset.offlineLabel || "Registro pendiente",
      kind: form.dataset.offlineQueue || "form",
      createdAt: new Date().toISOString(),
      fields: Array.from(formData.entries()),
    });
    writeOfflineQueue(queue);
    updateConnection();
  };
  const syncOfflineQueue = async () => {
    if (!navigator.onLine) return;
    let queue = readOfflineQueue();
    if (!queue.length) {
      updateConnection();
      return;
    }
    badge?.classList.add("offline");
    if (badge) badge.textContent = `Sincronizando ${queue.length} registro(s) pendiente(s)…`;
    let synced = 0;
    while (queue.length && navigator.onLine) {
      const item = queue[0];
      const body = new FormData();
      item.fields.forEach(([name, value]) => body.append(name, value));
      const csrfResponse = await fetch("/sesion/csrf/", {
        credentials: "same-origin",
        cache: "no-store",
        headers: {"X-Requested-With": "XMLHttpRequest"},
      });
      if (!csrfResponse.ok) break;
      const csrfPayload = await csrfResponse.json();
      body.set("csrfmiddlewaretoken", csrfPayload.csrfToken || "");
      const response = await fetch(item.url, {
        method: item.method || "POST",
        body,
        credentials: "same-origin",
        headers: {"X-Offline-Sync": "1"},
      });
      if (!response.ok) break;
      queue.shift();
      synced += 1;
      writeOfflineQueue(queue);
    }
    updateConnection();
    if (synced) location.reload();
  };

  addEventListener("online", updateConnection);
  addEventListener("online", () => syncOfflineQueue().catch(updateConnection));
  addEventListener("offline", updateConnection);
  updateConnection();
  if (navigator.onLine) syncOfflineQueue().catch(updateConnection);

  const productionReceptionDate = document.getElementById("id_reception_date");
  const productionPackagingDate = document.getElementById("id_packaging_date");
  const productionCustomerLot = document.getElementById("id_customer_lot");
  const productionSeries = document.getElementById("id_series");
  if (productionReceptionDate && productionPackagingDate && productionCustomerLot && productionSeries) {
    const pad = (value) => String(value).padStart(2, "0");
    const updateAutomaticProductionFields = () => {
      if (!productionReceptionDate.value) return;
      const [year, month, day] = productionReceptionDate.value.split("-").map(Number);
      const reception = new Date(year, month - 1, day);
      if (Number.isNaN(reception.getTime())) return;
      const packaging = new Date(reception);
      packaging.setDate(packaging.getDate() + 1);
      productionPackagingDate.value = [
        packaging.getFullYear(),
        pad(packaging.getMonth() + 1),
        pad(packaging.getDate()),
      ].join("-");
      productionCustomerLot.value = `PPF${pad(reception.getDate())}${pad(reception.getMonth() + 1)}${reception.getFullYear()}`;
      productionSeries.value = "001";
    };
    productionReceptionDate.addEventListener("change", updateAutomaticProductionFields);
    updateAutomaticProductionFields();
  }

  document.querySelectorAll('form[method="post"]').forEach((form) => {
    form.addEventListener("submit", async (event) => {
      if (form.dataset.csrfSubmitting === "1") return;
      const submitter = event.submitter;
      const submitAction = submitter?.getAttribute("formaction") || "";
      const submitMethod = submitter?.getAttribute("formmethod") || "";
      const confirmation = submitter?.dataset.confirmMessage || form.dataset.confirmMessage;
      if (confirmation && !window.confirm(confirmation)) {
        event.preventDefault();
        return;
      }
      if (form.dataset.offlineQueue && !navigator.onLine) {
        event.preventDefault();
        if (submitAction) {
          window.alert("Para eliminar necesita conexión a internet. El registro no fue modificado.");
          return;
        }
        queueOfflineSubmit(form, event.submitter);
        window.alert("Sin internet: guardé este envío en el equipo. Se sincronizará automáticamente cuando vuelva la conexión.");
        return;
      }
      event.preventDefault();
      if (submitter?.name) {
        const submitterInput = document.createElement("input");
        submitterInput.type = "hidden";
        submitterInput.name = submitter.name;
        submitterInput.value = submitter.value;
        form.appendChild(submitterInput);
      }
      if (submitAction) form.action = submitAction;
      if (submitMethod) form.method = submitMethod;
      const buttons = form.querySelectorAll('button[type="submit"],button:not([type])');
      buttons.forEach((button) => {
        button.disabled = true;
        button.dataset.label = button.textContent;
        button.textContent = "Guardando…";
      });
      try {
        await refreshCsrfToken(form);
        form.dataset.csrfSubmitting = "1";
        HTMLFormElement.prototype.submit.call(form);
      } catch (_) {
        buttons.forEach((button) => {
          button.disabled = false;
          button.textContent = button.dataset.label || "Guardar";
        });
        window.alert("No pude renovar la sesión. Recargue la página e inténtelo nuevamente.");
      }
    });
  });

  document.querySelectorAll("[data-rack-transition-button]").forEach((button) => {
    button.addEventListener("click", () => {
      const confirmation = button.dataset.confirmMessage;
      if (confirmation && !window.confirm(confirmation)) return;
      const reason = document.getElementById(button.dataset.reasonInput || "")?.value || "";
      const csrf = document.querySelector('input[name="csrfmiddlewaretoken"]')?.value || "";
      const form = document.createElement("form");
      form.method = "post";
      form.action = button.dataset.action || "";
      form.innerHTML = `<input type="hidden" name="csrfmiddlewaretoken" value="${csrf}"><input type="hidden" name="target_status" value="${button.dataset.targetStatus || ""}"><input type="hidden" name="reason" value="">`;
      form.querySelector('input[name="reason"]').value = reason;
      document.body.appendChild(form);
      form.submit();
    });
  });

  document.querySelectorAll("[data-save-and-close-rack]").forEach((button) => {
    button.addEventListener("click", () => {
      const form = document.getElementById(button.getAttribute("form"));
      if (!form) return;
      const product = document.getElementById(button.dataset.productInput || "")?.value || "";
      const trays = document.getElementById(button.dataset.trayInput || "")?.value || "";
      const capacity = document.getElementById(button.dataset.capacityInput || "")?.value || "";
      button.formNoValidate = true;
      [["product", product], ["tray_count", trays], ["max_trays", capacity]].forEach(([name, value]) => {
        if (value !== "") {
          const input = document.createElement("input");
          input.type = "hidden";
          input.name = name;
          input.value = value;
          form.appendChild(input);
        }
      });
    });
  });

  document.querySelectorAll("[data-save-rack]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      const form = button.closest("form");
      if (!form) return;
      const openRackInput = form.querySelector("[data-open-rack-input]");
      if (openRackInput) openRackInput.value = button.dataset.rackId || "";
      const saveRackInput = form.querySelector("[data-save-rack-input]");
      if (saveRackInput) saveRackInput.value = button.dataset.rackId || "";
      HTMLFormElement.prototype.submit.call(form);
    });
  });

  document.querySelectorAll("[data-rack-card]").forEach((card) => {
    const capacity = card.querySelector("[data-rack-capacity-select]");
    const total = card.querySelector("[data-rack-total]");
    if (capacity && total) {
      capacity.addEventListener("change", () => {
        total.textContent = `${total.dataset.currentTotal || "0"} / ${capacity.value}`;
        card.classList.toggle(
          "rack-card-complete",
          Number(total.dataset.currentTotal || 0) === Number(capacity.value)
        );
      });
    }

    const entryId = card.querySelector("[data-rack-entry-id]");
    const product = card.querySelector("[data-rack-product-select]");
    const trays = card.querySelector("[data-rack-tray-input]");
    const notice = card.querySelector("[data-editing-notice]");
    const noticeLabel = card.querySelector("[data-editing-label]");
    const cancel = card.querySelector("[data-cancel-entry-edit]");
    if (!entryId || !product || !trays || !notice || !noticeLabel || !cancel) return;

    const stopEditing = () => {
      entryId.value = "";
      product.value = "";
      trays.value = "";
      notice.classList.add("d-none");
      noticeLabel.textContent = "";
    };

    card.querySelectorAll("[data-edit-entry]").forEach((button) => {
      button.addEventListener("click", () => {
        entryId.value = button.dataset.entryId || "";
        product.value = button.dataset.productId || "";
        trays.value = button.dataset.trayCount || "";
        noticeLabel.textContent = button.dataset.entryLabel || "el registro";
        notice.classList.remove("d-none");
        product.focus();
      });
    });
    cancel.addEventListener("click", stopEditing);
  });

  document.querySelectorAll("[data-tunnel-crew-picker]").forEach((picker) => {
    const search = picker.querySelector("[data-tunnel-crew-search]");
    const select = picker.querySelector("[data-tunnel-crew-select]");
    const list = picker.querySelector("[data-tunnel-crew-choice-list]");
    if (!search || !select) return;

    const normalize = (value) => (value || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .trim();
    const options = Array.from(select.options)
      .filter((option) => option.value)
      .map((option) => ({
        value: option.value,
        label: (option.textContent || "").trim(),
        searchable: normalize(option.textContent || ""),
      }));

    const render = () => {
      const exact = options.find((option) => option.searchable === normalize(search.value));
      if (exact) select.value = exact.value;
      if (!list) return;
      const term = normalize(search.value);
      const visible = options
        .filter((option) => !term || option.searchable.includes(term))
        .slice(0, 12);
      list.innerHTML = "";
      visible.forEach((option) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "tunnel-crew-choice";
        button.textContent = option.label;
        button.dataset.value = option.value;
        button.setAttribute("aria-pressed", select.value === option.value ? "true" : "false");
        button.addEventListener("click", () => {
          select.value = option.value;
          search.value = option.label;
          render();
        });
        list.appendChild(button);
      });
      if (!visible.length) {
        const empty = document.createElement("span");
        empty.className = "tunnel-crew-empty";
        empty.textContent = "Sin coincidencias";
        list.appendChild(empty);
      }
    };

    search.addEventListener("input", () => {
      const exact = options.find((option) => option.searchable === normalize(search.value));
      select.value = exact ? exact.value : "";
      render();
    });
    search.addEventListener("focus", render);
    select.addEventListener("change", () => {
      const selected = options.find((option) => option.value === select.value);
      search.value = selected ? selected.label : "";
      render();
    });
    render();
  });

  document.querySelectorAll("[data-tunnel-product-picker]").forEach((picker) => {
    const search = picker.querySelector("[data-tunnel-product-search]");
    const select = document.getElementById(search?.dataset.productSelect || "");
    const suggestions = picker.querySelector("[data-tunnel-product-suggestions]");
    const picked = picker.querySelector("[data-tunnel-product-picked]");
    const lamina = picker.querySelector("[data-tunnel-product-lamina]");
    if (!search || !select || !suggestions || !picked) return;

    const normalize = (value) => (value || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .trim();
    const tokenise = (value) => normalize(value).split(/[^a-z0-9]+/).filter(Boolean);
    const splitLabel = (label) => {
      const parts = (label || "").split(" ? ");
      return [parts[0] || "", parts.slice(1).join(" ? ") || ""];
    };
    const matchesQuery = (option, term) => {
      if (!term) return true;
      if (option.searchable.includes(term)) return true;
      const qTokens = tokenise(term);
      if (!qTokens.length) return false;
      const tokens = tokenise(option.searchable);
      return qTokens.every((queryToken) => tokens.some((token) => token.includes(queryToken)));
    };
    const options = Array.from(select.options)
      .filter((option) => option.value)
      .map((option) => ({
        value: option.value,
        label: (option.textContent || "").trim(),
        searchable: normalize(option.textContent || ""),
        laminaColor: (option.dataset.laminaColor || "").trim(),
      }));

    const showLamina = (option) => {
      if (!lamina) return;
      lamina.replaceChildren();
      if (!option?.laminaColor) {
        lamina.hidden = true;
        return;
      }
      const chip = document.createElement("span");
      chip.className = "lamina-chip";
      chip.dataset.laminaColor = option.laminaColor;
      chip.textContent = `Lámina: ${option.laminaColor}`;
      lamina.appendChild(chip);
      lamina.hidden = false;
    };

    const syncSelection = () => {
      const term = normalize(search.value);
      const exact = options.find((option) => option.searchable === term || normalize(option.label) === term);
      if (exact) {
        select.value = exact.value;
        picked.textContent = `Seleccionado: ${exact.label}`;
        showLamina(exact);
        return exact;
      }
      select.value = "";
      picked.textContent = term ? "Escriba m?s para ver sugerencias con c?digo." : "Escriba para ver sugerencias con c?digo.";
      showLamina(null);
      return null;
    };

    const render = () => {
      const term = normalize(search.value);
      const visible = options.filter((option) => matchesQuery(option, term)).slice(0, 10);
      suggestions.innerHTML = "";
      suggestions.hidden = !term;
      if (term) {
        visible.forEach((option) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "tunnel-product-choice";
          const [code, name] = splitLabel(option.label);
          button.innerHTML = `<strong>${code}</strong>${name ? `<span>${name}</span>` : ""}`;
          if (option.laminaColor) {
            const chip = document.createElement("small");
            chip.className = "lamina-chip";
            chip.dataset.laminaColor = option.laminaColor;
            chip.textContent = option.laminaColor;
            button.appendChild(chip);
          }
          button.addEventListener("click", () => {
            select.value = option.value;
            search.value = option.label;
            picked.textContent = `Seleccionado: ${option.label}`;
            showLamina(option);
            render();
          });
          suggestions.appendChild(button);
        });
        if (!visible.length) {
          const empty = document.createElement("span");
          empty.className = "tunnel-product-empty";
          empty.textContent = "Sin coincidencias";
          suggestions.appendChild(empty);
        }
      }
      syncSelection();
    };

    search.addEventListener("input", render);
    search.addEventListener("focus", render);
    select.addEventListener("change", () => {
      const selected = options.find((option) => option.value === select.value);
      search.value = selected ? selected.label : "";
      picked.textContent = selected ? `Seleccionado: ${selected.label}` : "Escriba para ver sugerencias con c?digo.";
      showLamina(selected);
      render();
    });
    render();
  });

  document.querySelectorAll("[data-rack-card]").forEach((card) => {
    const crewEntryId = card.querySelector('input[name^="crew_entry_id_"]');
    const crewProduct = card.querySelector('select[name^="crew_product_"]');
    const crewSearch = card.querySelector('input[name^="crew_name_"]');
    const crewSelect = card.querySelector('select[name^="crew_"]');
    const crewTrays = card.querySelector('input[name^="crew_trays_"]');
    const crewNotice = card.querySelector("[data-crew-editing-notice]");
    const crewNoticeLabel = card.querySelector("[data-crew-editing-label]");
    const cancelCrewEdit = card.querySelector("[data-cancel-crew-edit]");
    if (!crewEntryId || !crewProduct || !crewSearch || !crewSelect || !crewTrays || !crewNotice || !crewNoticeLabel || !cancelCrewEdit) return;

    const stopCrewEditing = () => {
      crewEntryId.value = "";
      crewProduct.value = crewProduct.options[0]?.value || "";
      crewSearch.value = "";
      crewSelect.value = "";
      crewTrays.value = "";
      crewNotice.classList.add("d-none");
      crewNoticeLabel.textContent = "";
    };

    card.querySelectorAll("[data-edit-crew-entry]").forEach((button) => {
      button.addEventListener("click", () => {
        crewEntryId.value = button.dataset.crewEntryId || "";
        crewProduct.value = button.dataset.productId || "";
        crewSearch.value = button.dataset.crewName || "";
        crewSelect.value = button.dataset.crewId || "";
        crewTrays.value = button.dataset.trayCount || "";
        crewNoticeLabel.textContent = button.dataset.entryLabel || "la cuadrilla";
        crewNotice.classList.remove("d-none");
        crewSearch.focus();
      });
    });

    cancelCrewEdit.addEventListener("click", stopCrewEditing);
  });

  document.querySelectorAll("[data-crew-assign-all]").forEach((button) => {
    button.addEventListener("click", (event) => {
      const flagName = button.dataset.crewAssignAllFlag || "";
      const flag = flagName ? document.querySelector(`[name="${flagName}"]`) : null;
      if (flag) flag.value = "1";
    });
  });

  document.querySelectorAll("[data-module-carousel]").forEach((carousel) => {
    const strip = carousel.querySelector("[data-module-strip]");
    const previous = carousel.querySelector('[data-module-scroll="-1"]');
    const next = carousel.querySelector('[data-module-scroll="1"]');
    if (!strip || !previous || !next) return;

    const updateControls = () => {
      const maxScroll = Math.max(0, strip.scrollWidth - strip.clientWidth);
      previous.disabled = strip.scrollLeft <= 2;
      next.disabled = strip.scrollLeft >= maxScroll - 2;
    };
    const move = (direction) => {
      const distance = Math.max(220, strip.clientWidth * 0.78);
      strip.scrollBy({ left: direction * distance, behavior: "smooth" });
    };

    previous.addEventListener("click", () => move(-1));
    next.addEventListener("click", () => move(1));
    strip.addEventListener("scroll", updateControls, { passive: true });
    strip.addEventListener("keydown", (event) => {
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        move(event.key === "ArrowLeft" ? -1 : 1);
      }
    });
    addEventListener("resize", updateControls);
    requestAnimationFrame(updateControls);
  });

  document.querySelectorAll('select[name="position"]').forEach((select) => {
    const plaqueroFromLabel = (label) => {
      const match = label.match(/Plaquero\s+([123])/i);
      return match ? match[1] : "";
    };
    select.classList.add("plate-position-select");
    Array.from(select.options).forEach((option) => {
      const plaquero = plaqueroFromLabel(option.textContent || "");
      if (!plaquero) return;
      option.dataset.plaquero = plaquero;
      option.classList.add(`plate-position-option-${plaquero}`);
    });
    const updatePlatePositionColor = () => {
      select.classList.remove(
        "plate-position-selected-1",
        "plate-position-selected-2",
        "plate-position-selected-3"
      );
      const plaquero = select.selectedOptions[0]?.dataset.plaquero;
      if (plaquero) select.classList.add(`plate-position-selected-${plaquero}`);
    };
    select.addEventListener("change", updatePlatePositionColor);
    updatePlatePositionColor();
  });

  document.querySelectorAll("[data-plate-capture-form]").forEach((form) => {
    const position = form.querySelector('select[name="position"]');
    const startButton = form.querySelector("[data-plate-start-button]");
    const saveButton = form.querySelector("[data-plate-save-button]");
    const status = form.querySelector("[data-plate-capture-status]");
    const controlledFields = form.querySelectorAll(
      "[data-plate-capture-field] input, [data-plate-capture-field] select, [data-plate-capture-field] textarea"
    );
    const openPositions = new Set((form.dataset.openPositions || "").split(",").filter(Boolean));
    const completedPositions = new Set((form.dataset.completedPositions || "").split(",").filter(Boolean));
    const parsePositionMap = (raw) => new Map(
      (raw || "").split(";").filter(Boolean).map((item) => {
        const separator = item.indexOf("=");
        return [item.slice(0, separator), item.slice(separator + 1)];
      })
    );
    const positionShifts = parsePositionMap(form.dataset.positionShifts);
    const positionStarts = parsePositionMap(form.dataset.positionStarts);
    if (!position || !startButton || !saveButton || !status) return;

    const updatePlateCapture = () => {
      const selected = position.value || "";
      const isOpen = openPositions.has(selected);
      const isCompleted = completedPositions.has(selected);
      controlledFields.forEach((field) => {
        field.disabled = !isOpen;
      });
      form.classList.toggle("plate-capture-ready", isOpen);
      form.classList.toggle("plate-capture-locked", !isOpen);
      startButton.hidden = isOpen || isCompleted;
      startButton.disabled = !selected;
      saveButton.hidden = !isOpen;
      saveButton.disabled = !isOpen;
      if (!selected) {
        status.textContent = "Seleccione el plaquero antes de iniciar.";
      } else if (isCompleted) {
        status.textContent = "La carga de este plaquero ya fue finalizada.";
      } else if (isOpen) {
        const start = positionStarts.get(selected) || "hora registrada";
        const shift = positionShifts.get(selected) || "calculado";
        status.textContent = `Inicio ${start} · Turno ${shift}. Ya puede agregar productos y bandejas.`;
      } else {
        status.textContent = "Plaquero seleccionado. Pulse Iniciar llenado para habilitar los productos.";
      }
    };
    position.addEventListener("change", updatePlateCapture);
    updatePlateCapture();
  });

  // Prueba reversible: en móvil y escritorio solo queda abierto un plaquero a la vez.
  const plateFolds = Array.from(document.querySelectorAll("[data-plate-fold]"));
  plateFolds.forEach((fold) => {
    fold.addEventListener("toggle", () => {
      if (!fold.open) return;
      plateFolds.forEach((otherFold) => {
        if (otherFold !== fold) otherFold.open = false;
      });
    });
  });

  if ("serviceWorker" in navigator) {
    addEventListener("load", async () => {
      try {
        const registration = await navigator.serviceWorker.register(
          "/service-worker.js?v=20260724-delete-action-2",
          {updateViaCache: "none"}
        );
        await registration.update();
      } catch (_) {}
    });
  }
})();
