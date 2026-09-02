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
    button.addEventListener("click", (event) => {
      const card = button.closest("[data-rack-card]");
      const editor = rackMultiselectors.get(card?.querySelector("[data-rack-selector]")?.dataset.rackId || "");
      if (editor) {
        if (!editor.ready()) {
          event.preventDefault();
          return;
        }
        if (editor.extrasCount() > 0) {
          event.preventDefault();
          editor.flash('Primero guarde los productos adicionales con «Guardar productos» y luego cierre el rack.');
          return;
        }
      }
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
      const editor = rackMultiselectors.get(button.dataset.rackId || "");
      if (editor && !editor.ready()) {
        return;
      }
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

  const rackMultiselectors = window.__rackMultiselectors || (window.__rackMultiselectors = new Map());

  document.querySelectorAll("[data-rack-selector]").forEach((trigger) => {
    const card = trigger.closest("[data-rack-card]");
    if (!card || card.classList.contains("rack-card-closed")) return;

    const panel = card.querySelector("[data-rack-multiselect]");
    const search = card.querySelector("[data-rack-multiselect-search]");
    const listEl = card.querySelector("[data-rack-multiselect-list]");
    const countEl = card.querySelector("[data-rack-multiselect-count]");
    const doneBtn = card.querySelector("[data-rack-multiselect-done]");
    const labelEl = card.querySelector("[data-rack-selector-label]");
    const editor = card.querySelector("[data-rack-tray-editor]");
    const totalNode = card.querySelector("[data-rack-editor-total]");
    const capacityNode = card.querySelector("[data-rack-editor-capacity]");
    const warningNode = card.querySelector("[data-rack-tray-warning]");
    const mainSelect = card.querySelector("[data-rack-product-select]");
    const mainTray = card.querySelector("[data-rack-tray-input]");
    const capacitySelect = card.querySelector("[data-rack-capacity-select]");
    const entryIdInput = card.querySelector("[data-rack-entry-id]");
    const rackTotalNode = card.querySelector("[data-rack-total]");
    if (!panel || !search || !listEl || !countEl || !doneBtn || !labelEl || !editor || !totalNode || !capacityNode || !warningNode || !mainSelect || !mainTray || !capacitySelect || !entryIdInput) return;

    const normalize = (value) => (value || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .trim();
    const tokenise = (value) => normalize(value).split(/[^a-z0-9]+/).filter(Boolean);
    const matchesQuery = (option, term) => {
      if (!term) return true;
      if (option.searchable.includes(term)) return true;
      const queryTokens = tokenise(term);
      if (!queryTokens.length) return false;
      const tokens = tokenise(option.searchable);
      return queryTokens.every((queryToken) => tokens.some((token) => token.includes(queryToken)));
    };
    const splitProductLabel = (label) => {
      const parts = (label || "").split(" ? ");
      if (parts.length < 2) return [parts[0] || "", ""];
      return [parts[0], parts.slice(1).join(" ? ")];
    };

    const formIndex = (entryIdInput.name.match(/^racks-(\d+)-/) || [])[1] || "0";
    const options = Array.from(mainSelect.options)
      .filter((option) => option.value)
      .map((option) => {
        const [code, name] = splitProductLabel(option.textContent || "");
        return {
          value: option.value,
          code,
          name,
          label: (option.textContent || "").trim(),
          searchable: normalize(`${code} ${name}`),
          laminaColor: (option.dataset.laminaColor || "").trim(),
        };
      });
    const optionByValue = (value) => options.find((option) => option.value === String(value));

    const saved = new Map();
    card.querySelectorAll("[data-edit-entry]").forEach((button) => {
      saved.set(button.dataset.productId, {
        trayCount: Number(button.dataset.trayCount || 0),
        entryId: button.dataset.entryId || "",
      });
    });

    const selection = new Map();

    const rows = new Map();
    let panelOpen = false;

    const updateLabel = () => {
      const size = selection.size;
      if (!size) {
        labelEl.textContent = "Seleccione los productos";
        return;
      }
      const first = optionByValue(selection.keys().next().value);
      labelEl.textContent = size === 1 && first
        ? first.label
        : `${size} productos seleccionados`;
    };

    const currentCapacity = () => {
      const value = Number(capacitySelect.value);
      return Number.isFinite(value) ? value : Number(capacityNode.textContent || 0);
    };

    const updateTotals = () => {
      let incoming = 0;
      let replaced = 0;
      selection.forEach((_, pid) => {
        const row = rows.get(pid);
        const value = row ? parseInt(row.input.value, 10) : 0;
        incoming += Number.isFinite(value) && value > 0 ? value : 0;
        if (saved.has(pid)) replaced += saved.get(pid).trayCount;
      });
      const base = Number(rackTotalNode?.dataset.currentTotal || 0);
      const capacity = currentCapacity();
      capacityNode.textContent = capacity;
      const total = base - replaced + incoming;
      totalNode.textContent = total;
      const exceeded = total > capacity;
      totalNode.classList.toggle("rack-editor-total-exceed", exceeded);
      if (exceeded) {
        warningNode.textContent = "La cantidad total de bandejas supera la capacidad del rack.";
        warningNode.classList.remove("d-none");
      } else {
        warningNode.classList.add("d-none");
      }
      return { total, capacity, exceeded };
    };

    const flash = (message) => {
      warningNode.textContent = message;
      warningNode.classList.remove("d-none");
    };

    const renderRows = () => {
      editor.replaceChildren();
      rows.clear();
      selection.forEach((_, pid) => {
        const option = optionByValue(pid);
        if (!option) return;
        const isSaved = saved.has(pid);
        const rowEl = document.createElement("div");
        rowEl.className = `rack-tray-row${isSaved ? " is-saved" : ""}`;
        const nameCell = document.createElement("div");
        nameCell.className = "rack-tray-name";
        const nameLabel = document.createElement("strong");
        nameLabel.textContent = option.label;
        const nameSmall = document.createElement("small");
        nameSmall.textContent = isSaved ? "Ya guardado en el rack" : "Producto nuevo en el rack";
        nameCell.append(nameLabel, nameSmall);
        if (option.laminaColor) {
          const chip = document.createElement("small");
          chip.className = "lamina-chip";
          chip.dataset.laminaColor = option.laminaColor;
          chip.textContent = `Lámina: ${option.laminaColor}`;
          nameCell.appendChild(chip);
        }
        const fieldCell = document.createElement("div");
        fieldCell.className = "rack-tray-field";
        const fieldLabel = document.createElement("label");
        fieldLabel.textContent = "Bandejas";
        const input = document.createElement("input");
        input.type = "number";
        input.min = "1";
        input.inputmode = "numeric";
        input.placeholder = "0";
        input.className = "form-control form-control-sm";
        input.value = isSaved ? String(saved.get(pid).trayCount) : "";
        input.addEventListener("input", () => {
          updateTotals();
        });
        input.addEventListener("keydown", (event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            const button = card.querySelector("[data-save-rack]");
            if (button) button.click();
          }
        });
        fieldCell.append(fieldLabel, input);
        rowEl.append(nameCell, fieldCell);
        if (!isSaved) {
          const removeButton = document.createElement("button");
          removeButton.type = "button";
          removeButton.className = "rack-tray-remove";
          removeButton.textContent = "✕";
          removeButton.title = "Quitar este producto";
          removeButton.addEventListener("click", () => {
            selection.delete(pid);
            renderRows();
            renderList();
            updateLabel();
            updateTotals();
          });
          rowEl.appendChild(removeButton);
        }
        editor.appendChild(rowEl);
        rows.set(pid, { element: rowEl, input });
      });
    };

    const renderList = () => {
      const term = normalize(search.value);
      listEl.innerHTML = "";
      const visible = options.filter((option) => matchesQuery(option, term));
      if (!visible.length) {
        const empty = document.createElement("li");
        empty.className = "rack-multiselect-empty";
        empty.textContent = "Sin coincidencias";
        listEl.appendChild(empty);
        return;
      }
      visible.forEach((option) => {
        const isSaved = saved.has(option.value);
        const isOn = selection.has(option.value);
        const item = document.createElement("li");
        item.className = `rack-multiselect-option${isOn ? " is-on" : ""}${isSaved ? " is-saved" : ""}`;
        const mark = document.createElement("span");
        mark.className = "rack-multiselect-mark";
        mark.textContent = isOn ? "✓" : "";
        item.appendChild(mark);
        const nameCell = document.createElement("span");
        nameCell.className = "rack-multiselect-name";
        const strong = document.createElement("strong");
        strong.textContent = option.code || option.label;
        const span = document.createElement("span");
        span.textContent = option.name;
        nameCell.append(strong, span);
        item.appendChild(nameCell);
        if (!isOn && option.laminaColor) {
          const chip = document.createElement("small");
          chip.className = "lamina-chip rack-multiselect-lamchip";
          chip.dataset.laminaColor = option.laminaColor;
          chip.textContent = option.laminaColor;
          item.appendChild(chip);
        }
        if (isSaved) {
          const note = document.createElement("small");
          note.className = "rack-multiselect-locked";
          note.textContent = "Ya guardado";
          item.appendChild(note);
        }
        item.addEventListener("click", () => {
          if (isSaved) return;
          if (isOn) selection.delete(option.value); else selection.set(option.value, true);
          renderList();
          renderRows();
          updateLabel();
          updateTotals();
          search.focus();
        });
        listEl.appendChild(item);
      });
      const size = selection.size;
      countEl.textContent = `${size} producto${size === 1 ? "" : "s"} seleccionado${size === 1 ? "" : "s"}`;
    };

    const openPanel = () => {
      panelOpen = true;
      trigger.setAttribute("aria-expanded", "true");
      panel.classList.remove("d-none");
      search.value = "";
      renderList();
      search.focus();
    };
    const closePanel = () => {
      panelOpen = false;
      trigger.setAttribute("aria-expanded", "false");
      panel.classList.add("d-none");
    };

    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      if (panelOpen) closePanel(); else openPanel();
    });
    search.addEventListener("input", renderList);
    search.addEventListener("keydown", (event) => {
      if (event.key === "Enter") event.preventDefault();
    });
    doneBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      closePanel();
    });
    document.addEventListener("mousedown", (event) => {
      if (!panelOpen) return;
      const wasInPanel = panel.contains(event.target);
      const wasOnTrigger = trigger.contains(event.target);
      if (!wasInPanel && !wasOnTrigger) closePanel();
    });

    const mainPid = () => {
      if (entryIdInput.value) {
        for (const pid of selection.keys()) {
          if (saved.has(pid) && saved.get(pid).entryId === entryIdInput.value) return pid;
        }
      }
      return selection.keys().next().value;
    };

    const syncPayload = () => {
      const main = mainPid();
      let extrasCount = 0;
      for (const pid of selection.keys()) {
        const row = rows.get(pid);
        const tray = row ? String(row.input.value || "") : "";
        if (String(pid) === String(main)) {
          mainSelect.value = pid;
          mainTray.value = tray;
          continue;
        }
        let productInput = card.querySelector(`[data-extra-product="${pid}"]`);
        let trayInput = card.querySelector(`[data-extra-trays="${pid}"]`);
        if (!productInput || !trayInput) {
          productInput = document.createElement("input");
          productInput.type = "hidden";
          productInput.dataset.extraProduct = pid;
          trayInput = document.createElement("input");
          trayInput.type = "hidden";
          trayInput.dataset.extraTrays = pid;
          card.appendChild(productInput);
          card.appendChild(trayInput);
        }
        productInput.name = `racks-${formIndex}-extra_product_${extrasCount}`;
        trayInput.name = `racks-${formIndex}-extra_trays_${extrasCount}`;
        productInput.value = pid;
        trayInput.value = tray;
        extrasCount += 1;
      }
      return { ok: true, extrasCount, main };
    };

    const prepareSave = () => {
      if (!selection.size) {
        flash("Seleccione un producto antes de guardar.");
        return { ok: false };
      }
      for (const [, row] of rows) {
        if (!Number.isFinite(parseInt(row.input.value, 10)) || parseInt(row.input.value, 10) < 1) {
          flash("Ingrese la cantidad de bandejas de cada producto seleccionado.");
          return { ok: false };
        }
      }
      const result = syncPayload();
      if (!result.ok) {
        flash(result.message || "No se pudo preparar el guardado.");
        return { ok: false };
      }
      const totals = updateTotals();
      if (totals.exceeded) {
        flash("La cantidad total de bandejas supera la capacidad del rack.");
        return { ok: false };
      }
      return { ok: true, extrasCount: result.extrasCount };
    };

    rackMultiselectors.set(trigger.dataset.rackId, {
      ready: () => {
        const result = prepareSave();
        return result && result.ok;
      },
      extrasCount: () => {
        const result = prepareSave();
        if (!result || !result.ok) return 1;
        let pending = 0;
        for (const pid of selection.keys()) {
          if (String(pid) === String(result.main)) continue;
          if (!saved.has(pid)) pending += 1;
        }
        return pending;
      },
      flash,
    });

    let editingPid = "";
    card.querySelectorAll("[data-edit-entry]").forEach((button) => {
      button.addEventListener("click", () => {
        editingPid = button.dataset.productId || "";
        if (editingPid) selection.set(editingPid, true);
        renderRows();
        renderList();
        updateLabel();
        updateTotals();
      });
    });
    const cancelEdit = card.querySelector("[data-cancel-entry-edit]");
    if (cancelEdit) cancelEdit.addEventListener("click", () => {
      if (editingPid) selection.delete(editingPid);
      editingPid = "";
      entryIdInput.value = "";
      mainSelect.value = "";
      mainTray.value = "";
      renderRows();
      renderList();
      updateLabel();
      updateTotals();
    });

    renderRows();
    if (mainSelect.value && mainTray.value) {
      const mainRow = rows.get(mainSelect.value);
      if (mainRow) mainRow.input.value = mainTray.value;
    }
    updateLabel();
    updateTotals();
  });

  document.querySelectorAll("[data-plate-product-editor]").forEach((container) => {
    const form = container.closest("form");
    if (!form) return;
    const trigger = container.querySelector("[data-plate-selector]");
    const panel = container.querySelector("[data-plate-multiselect]");
    const search = container.querySelector("[data-plate-multiselect-search]");
    const listEl = container.querySelector("[data-plate-multiselect-list]");
    const countEl = container.querySelector("[data-plate-multiselect-count]");
    const doneBtn = container.querySelector("[data-plate-multiselect-done]");
    const labelEl = container.querySelector("[data-plate-selector-label]");
    const editor = container.querySelector("[data-plate-tray-editor]");
    const totalNode = container.querySelector("[data-plate-editor-total]");
    const capacityNode = container.querySelector("[data-plate-editor-capacity]");
    const warningNode = container.querySelector("[data-plate-tray-warning]");
    const mainSelect = form.querySelector('select[name="product"]');
    const mainTray = form.querySelector('input[name="tray_count"]');
    const positionSelect = form.querySelector('select[name="position"]');
    if (!trigger || !panel || !search || !listEl || !countEl || !doneBtn || !labelEl || !editor || !totalNode || !capacityNode || !warningNode || !mainSelect || !mainTray || !positionSelect) return;

    const parsePairMap = (raw) => new Map(
      String(raw || "").split(";").filter(Boolean).map((item) => {
        const separator = item.indexOf("=");
        return [item.slice(0, separator), Number(item.slice(separator + 1)) || 0];
      })
    );
    const laminaColors = new Map(
      String(container.dataset.plateLaminaColors || "").split(";").filter(Boolean).map((item) => {
        const separator = item.indexOf("=");
        return [item.slice(0, separator), item.slice(separator + 1)];
      })
    );
    const capacities = parsePairMap(container.dataset.plateCaps);
    const totals = parsePairMap(container.dataset.plateTotals);
    const savedByPosition = new Map();
    String(container.dataset.plateSaved || "").split("|").filter(Boolean).forEach((block) => {
      const separator = block.indexOf(":");
      const positionId = block.slice(0, separator);
      const entries = new Map();
      block.slice(separator + 1).split(";").filter(Boolean).forEach((payload) => {
        const [pid, trayText, entryId] = payload.split("=");
        entries.set(pid, { trayCount: Number(trayText) || 0, entryId: entryId || "" });
      });
      savedByPosition.set(positionId, entries);
    });

    const normalize = (value) => (value || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .trim();
    const tokenise = (value) => normalize(value).split(/[^a-z0-9]+/).filter(Boolean);
    const matchesQuery = (option, term) => {
      if (!term) return true;
      if (option.searchable.includes(term)) return true;
      const queryTokens = tokenise(term);
      if (!queryTokens.length) return false;
      const tokens = tokenise(option.searchable);
      return queryTokens.every((queryToken) => tokens.some((token) => token.includes(queryToken)));
    };

    const options = Array.from(mainSelect.options)
      .filter((option) => option.value)
      .map((option) => ({
        value: option.value,
        label: (option.textContent || "").trim(),
        searchable: normalize(option.textContent || ""),
        laminaColor: laminaColors.get(option.value) || "",
      }));
    const optionByValue = (value) => options.find((option) => option.value === String(value));

    const selection = new Map();
    let saved = new Map();
    let panelOpen = false;
    let capacity = 189;

    const currentPosition = () => positionSelect.value || "";
    const loadPositionState = () => {
      const positionId = currentPosition();
      saved = savedByPosition.get(positionId) || new Map();
      capacity = capacities.get(positionId) || 189;
      trigger.disabled = !positionId;
      selection.clear();
      renderRows();
      renderList();
      updateLabel();
      updateTotals();
    };

    const updateLabel = () => {
      const size = selection.size;
      if (!size) {
        labelEl.textContent = "Seleccione los productos";
        return;
      }
      const first = optionByValue(selection.keys().next().value);
      labelEl.textContent = size === 1 && first
        ? first.label
        : `${size} productos seleccionados`;
    };

    const currentBaseTotal = () => {
      const positionId = currentPosition();
      const base = totals.get(positionId) || 0;
      let incoming = 0;
      selection.forEach((_, pid) => {
        const row = rowsByPid.get(pid);
        const value = row ? parseInt(row.input.value, 10) : 0;
        incoming += Number.isFinite(value) && value > 0 ? value : 0;
      });
      return base + incoming;
    };
    const rowsByPid = new Map();

    const updateTotals = () => {
      const positionId = currentPosition();
      const base = totals.get(positionId) || 0;
      let incoming = 0;
      let replaced = 0;
      selection.forEach((_, pid) => {
        const row = rowsByPid.get(pid);
        const value = row ? parseInt(row.input.value, 10) : 0;
        if (Number.isFinite(value) && value > 0) incoming += value;
        if (saved.has(pid)) replaced += saved.get(pid).trayCount;
      });
      capacityNode.textContent = capacity;
      const total = base - replaced + incoming;
      totalNode.textContent = total;
      const exceeded = total > capacity;
      totalNode.classList.toggle("rack-editor-total-exceed", exceeded);
      if (exceeded) {
        warningNode.textContent = "La cantidad total de bandejas supera la capacidad del plaquero.";
        warningNode.classList.remove("d-none");
      } else {
        warningNode.classList.add("d-none");
      }
      return { total, capacity, exceeded };
    };

    const flash = (message) => {
      warningNode.textContent = message;
      warningNode.classList.remove("d-none");
    };

    const renderRows = () => {
      editor.replaceChildren();
      rowsByPid.clear();
      selection.forEach((_, pid) => {
        const option = optionByValue(pid);
        if (!option) return;
        const isSaved = saved.has(pid);
        const rowEl = document.createElement("div");
        rowEl.className = `rack-tray-row${isSaved ? " is-saved" : ""}`;
        const nameCell = document.createElement("div");
        nameCell.className = "rack-tray-name";
        const nameLabel = document.createElement("strong");
        nameLabel.textContent = option.label;
        const nameSmall = document.createElement("small");
        nameSmall.textContent = isSaved ? "Ya guardado en el plaquero" : "Producto nuevo en el plaquero";
        nameCell.append(nameLabel, nameSmall);
        if (option.laminaColor) {
          const chip = document.createElement("small");
          chip.className = "lamina-chip";
          chip.dataset.laminaColor = option.laminaColor;
          chip.textContent = `Lámina: ${option.laminaColor}`;
          nameCell.appendChild(chip);
        }
        const fieldCell = document.createElement("div");
        fieldCell.className = "rack-tray-field";
        const fieldLabel = document.createElement("label");
        fieldLabel.textContent = "Bandejas";
        const input = document.createElement("input");
        input.type = "number";
        input.min = "1";
        input.inputmode = "numeric";
        input.placeholder = "0";
        input.className = "form-control form-control-sm";
        input.value = isSaved ? String(saved.get(pid).trayCount) : "";
        input.addEventListener("input", () => {
          updateTotals();
        });
        fieldCell.append(fieldLabel, input);
        rowEl.append(nameCell, fieldCell);
        if (!isSaved) {
          const removeButton = document.createElement("button");
          removeButton.type = "button";
          removeButton.className = "rack-tray-remove";
          removeButton.textContent = "✕";
          removeButton.title = "Quitar este producto";
          removeButton.addEventListener("click", () => {
            selection.delete(pid);
            renderRows();
            renderList();
            updateLabel();
            updateTotals();
          });
          rowEl.appendChild(removeButton);
        }
        editor.appendChild(rowEl);
        rowsByPid.set(pid, { element: rowEl, input });
      });
    };

    const renderList = () => {
      const term = normalize(search.value);
      listEl.innerHTML = "";
      const visible = options.filter((option) => matchesQuery(option, term));
      if (!visible.length) {
        const empty = document.createElement("li");
        empty.className = "rack-multiselect-empty";
        empty.textContent = "Sin coincidencias";
        listEl.appendChild(empty);
        return;
      }
      visible.forEach((option) => {
        const isSaved = saved.has(option.value);
        const isOn = selection.has(option.value);
        const item = document.createElement("li");
        item.className = `rack-multiselect-option${isOn ? " is-on" : ""}${isSaved ? " is-saved" : ""}`;
        const mark = document.createElement("span");
        mark.className = "rack-multiselect-mark";
        mark.textContent = isOn ? "✓" : "";
        item.appendChild(mark);
        const nameCell = document.createElement("span");
        nameCell.className = "rack-multiselect-name";
        const strong = document.createElement("strong");
        strong.textContent = option.code || option.label;
        const span = document.createElement("span");
        span.textContent = option.name;
        nameCell.append(strong, span);
        item.appendChild(nameCell);
        if (!isOn && option.laminaColor) {
          const chip = document.createElement("small");
          chip.className = "lamina-chip rack-multiselect-lamchip";
          chip.dataset.laminaColor = option.laminaColor;
          chip.textContent = option.laminaColor;
          item.appendChild(chip);
        }
        if (isSaved) {
          const note = document.createElement("small");
          note.className = "rack-multiselect-locked";
          note.textContent = "Ya guardado";
          item.appendChild(note);
        }
        item.addEventListener("click", () => {
          if (isSaved) return;
          if (isOn) selection.delete(option.value); else selection.set(option.value, true);
          renderList();
          renderRows();
          updateLabel();
          updateTotals();
          search.focus();
        });
        listEl.appendChild(item);
      });
      const size = selection.size;
      countEl.textContent = `${size} producto${size === 1 ? "" : "s"} seleccionado${size === 1 ? "" : "s"}`;
    };

    const openPanel = () => {
      panelOpen = true;
      trigger.setAttribute("aria-expanded", "true");
      panel.classList.remove("d-none");
      search.value = "";
      renderList();
      search.focus();
    };
    const closePanel = () => {
      panelOpen = false;
      trigger.setAttribute("aria-expanded", "false");
      panel.classList.add("d-none");
    };

    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      if (panelOpen) closePanel(); else openPanel();
    });
    search.addEventListener("input", renderList);
    search.addEventListener("keydown", (event) => {
      if (event.key === "Enter") event.preventDefault();
    });
    doneBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      closePanel();
    });
    document.addEventListener("mousedown", (event) => {
      if (!panelOpen) return;
      const wasInPanel = panel.contains(event.target);
      const wasOnTrigger = trigger.contains(event.target);
      if (!wasInPanel && !wasOnTrigger) closePanel();
    });

    const syncPayload = () => {
      const first = selection.keys().next().value;
      let extrasCount = 0;
      for (const pid of selection.keys()) {
        const row = rowsByPid.get(pid);
        const tray = row ? String(row.input.value || "") : "";
        if (String(pid) === String(first)) {
          mainSelect.value = pid;
          mainTray.value = tray;
          continue;
        }
        let productInput = form.querySelector(`[data-plate-extra-product="${pid}"]`);
        let trayInput = form.querySelector(`[data-plate-extra-trays="${pid}"]`);
        if (!productInput || !trayInput) {
          productInput = document.createElement("input");
          productInput.type = "hidden";
          productInput.dataset.plateExtraProduct = pid;
          trayInput = document.createElement("input");
          trayInput.type = "hidden";
          trayInput.dataset.plateExtraTrays = pid;
          form.appendChild(productInput);
          form.appendChild(trayInput);
        }
        productInput.name = `extra_product_${extrasCount}`;
        trayInput.name = `extra_trays_${extrasCount}`;
        productInput.value = pid;
        trayInput.value = tray;
        extrasCount += 1;
      }
      return { ok: true, extrasCount };
    };

    const initialPayload = () => {
      if (!selection.size) return { ok: false, message: "Seleccione al menos un producto." };
      if (!currentPosition()) return { ok: false, message: "Seleccione el plaquero antes de guardar." };
      return { ok: true };
    };

    const prepareSave = () => {
      for (const [, row] of rowsByPid) {
        if (!Number.isFinite(parseInt(row.input.value, 10)) || parseInt(row.input.value, 10) < 1) {
          flash("Ingrese la cantidad de bandejas de cada producto seleccionado.");
          return { ok: false };
        }
      }
      if (!selection.size) {
        flash("Seleccione al menos un producto para guardar.");
        return { ok: false };
      }
      if (!currentPosition()) {
        flash("Seleccione el plaquero antes de guardar.");
        return { ok: false };
      }
      const result = initialPayload();
      if (!result.ok) {
        flash(result.message || "No se pudo preparar el guardado.");
        return { ok: false };
      }
      const totals = updateTotals();
      if (totals.exceeded) {
        flash("La cantidad total de bandejas supera la capacidad del plaquero.");
        return { ok: false };
      }
      syncPayload();
      return { ok: true };
    };

    const saveButton = form.querySelector("[data-plate-save-button]");
    if (saveButton) {
      saveButton.addEventListener("click", (event) => {
        if (!form.classList.contains("plate-capture-ready")) return;
        const result = prepareSave();
        if (!result.ok) {
          event.preventDefault();
          return;
        }
      });
    }
    positionSelect.addEventListener("change", () => {
      doneBtn.click();
      loadPositionState();
    });
    loadPositionState();
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
          "/service-worker.js?v=20260902-realtime-poll-v2",
          {updateViaCache: "none"}
        );
        await registration.update();
      } catch (_) {}
    });
  }
})();

(() => {
  if (document.body.classList.contains("login-page")) return;
  const POLL_INTERVAL_MS = 3000;
  const heartbeatUrl = "/sync/heartbeat/";
  let knownLastTimestamp = undefined;
  let checking = false;

  const isEditingForm = () => {
    const el = document.activeElement;
    if (!el) return false;
    const tag = el.tagName;
    if (tag !== "INPUT" && tag !== "TEXTAREA" && tag !== "SELECT") return false;
    if (tag === "INPUT" && (el.type === "submit" || el.type === "button")) return false;
    return true;
  };

  const checkForUpdates = async () => {
    if (checking) return;
    checking = true;
    try {
      const res = await fetch(heartbeatUrl, { credentials: "same-origin", cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      if (knownLastTimestamp === undefined) {
        knownLastTimestamp = data.last_timestamp;
        return;
      }
      if (data.last_timestamp !== knownLastTimestamp) {
        if (isEditingForm()) {
          knownLastTimestamp = data.last_timestamp;
          return;
        }
        window.location.reload();
      }
    } catch (_) {
      // sin red: se reintenta en el siguiente ciclo
    } finally {
      checking = false;
    }
  };

  setInterval(checkForUpdates, POLL_INTERVAL_MS);
  checkForUpdates();
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) checkForUpdates();
  });
})();
