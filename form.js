// Dynamic cost-estimate notice for the Llama.cpp WebUI form.
//
// Shows a notice box under "Number of hours":
//   "This job is estimated to cost up to $X.XX if run to completion."
//
// cost = GPUs (always 1) x hours x rate(partition)
//   gpu-h200      -> $0.90 / GPU-hour
//   gpu-h200-mig  -> $0.13 / GPU-hour
//
// OOD inlines this file on the form page (form.js). Field ids are
// lowercase attribute names on OOD >= 4.0, so look them up by id
// first and fall back to the name attribute.
(function () {
  'use strict';

  var RATES = {
    'gpu-h200': 0.90,
    'gpu-h200-mig': 0.13
  };
  var GPUS = 1; // the job always requests exactly 1 GPU
  var NOTE_ID = 'llama-cost-note';
  var MODEL_NOTE_ID = 'llama-model-availability-note';
  // Add an attribute name here to put its form row behind the same checkbox.
  var ADVANCED_FIELDS = [
    'temperature',
    'extra_llama_args',
    'extra_slurm_args'
  ];

  function findField(name) {
    return document.getElementById(name) ||
           document.querySelector('[name="' + name + '"]') ||
           document.querySelector('[id$="_' + name + '"]') ||
           document.querySelector('[name$="[' + name + ']"]');
  }

  function fieldWrapper(field) {
    return (field.closest && field.closest('.form-group')) ||
           field.parentElement ||
           field.parentNode;
  }

  function updateAdvancedVisibility() {
    var checkbox = findField('enable_advanced_args');
    if (!checkbox) { return false; }

    var foundAll = true;
    ADVANCED_FIELDS.forEach(function (name) {
      var field = findField(name);
      if (!field) {
        foundAll = false;
        return;
      }

      var wrapper = fieldWrapper(field);
      if (wrapper) {
        wrapper.style.display = checkbox.checked ? '' : 'none';
      }
    });

    return foundAll;
  }

  function bindAdvanced() {
    var checkbox = findField('enable_advanced_args');
    if (!checkbox || checkbox.getAttribute('data-llama-advanced-bound') === 'true') {
      return;
    }

    checkbox.setAttribute('data-llama-advanced-bound', 'true');
    checkbox.addEventListener('change', updateAdvancedVisibility);
  }

  function layoutMountRows() {
    // Build a simple table-like layout for mount rows: Source | Destination | Writable?
    var headerAdded = document.getElementById('mount-table-header');
    var helpAdded   = document.getElementById('mount-table-help');
    // Add header row once
    if (!headerAdded) {
      var src0 = findField('mount1_source');
      if (src0) {
        var ref = fieldWrapper(src0).parentNode;
        var header = document.createElement('div');
        header.id = 'mount-table-header';
        header.className = 'row mb-1 font-weight-bold';
        header.innerHTML = '<div class="col-sm-5">Source (Tillicum)</div><div class="col-sm-5">Destination (Container)</div><div class="col-sm-2 text-center">Writable</div>';
        ref.insertBefore(header, fieldWrapper(src0));
      }
    }
    for (var i = 1; i <= 3; i += 1) {
      var src = findField('mount' + i + '_source');
      var dst = findField('mount' + i + '_dest');
      var wr = findField('mount' + i + '_writable');
      if (!src || !dst || !wr) { continue; }
      var wSrc = fieldWrapper(src);
      var wDst = fieldWrapper(dst);
      var wWr  = fieldWrapper(wr);
      if (!wSrc || !wDst || !wWr) { continue; }
      // Avoid re-wrapping
      if (wSrc.parentElement && wSrc.parentElement.getAttribute('data-mount-row') === String(i)) { continue; }
      var row = document.createElement('div');
      row.className = 'row mb-2';
      row.setAttribute('data-mount-row', String(i));
      // Make wrappers behave as columns
      wSrc.className = 'form-group col-sm-5';
      wDst.className = 'form-group col-sm-5';
      wWr.className  = 'form-group col-sm-2 d-flex align-items-center justify-content-center';
      // Insert row before the first wrapper to keep order
      var ref = wSrc.parentNode;
      ref.insertBefore(row, wSrc);
      row.appendChild(wSrc);
      row.appendChild(wDst);
      row.appendChild(wWr);
      // Hide field labels – column headers are provided by the table header row
      var srcLabel = wSrc.querySelector('label');
      var dstLabel = wDst.querySelector('label');
      if (srcLabel) srcLabel.style.display = 'none';
      if (dstLabel) dstLabel.style.display = 'none';
      // Writable checkbox: hide label text and vertically center the box
      var wrLabel = wWr.querySelector('label');
      if (wrLabel) {
        wrLabel.className = 'mb-0';
        // Remove label text, keep only the input
        for (var n = wrLabel.childNodes.length - 1; n >= 0; n--) {
          var node = wrLabel.childNodes[n];
          if (node.nodeType === 3) {
            node.textContent = '';
          }
        }
        wWr.style.display = 'flex';
        wWr.style.alignItems = 'center';
        wWr.style.justifyContent = 'center';
      }
    }
    // Add merged help paragraph once, after the last row
    if (!helpAdded) {
      var lastWr = findField('mount3_writable');
      if (lastWr) {
        var wLast = fieldWrapper(lastWr);
        var help = document.createElement('div');
        help.id = 'mount-table-help';
        help.className = 'text-muted small mt-2';
        help.textContent = 'Manually add up to 3 mount points. Read-only by default. Source path on Tillicum to bind into the container. Destination path inside the container. If destination is left blank, destination matches source (i.e., it will appear exactly like it does on the cluster). If "writable" is unchecked, the mount is read-only.';
        // Insert after the row containing the last wrapper
        var rowParent = wLast.parentElement;
        if (rowParent) {
          rowParent.parentNode.insertBefore(help, rowParent.nextSibling);
        } else if (wLast.parentNode) {
          wLast.parentNode.parentNode.insertBefore(help, wLast.parentNode.nextSibling);
        }
      }
    }
  }

  function modelNoteBox(modelField) {
    var box = document.getElementById(MODEL_NOTE_ID);
    if (box) { return box; }

    box = document.createElement('div');
    box.id = MODEL_NOTE_ID;
    box.className = 'alert alert-warning mt-2';
    box.style.display = 'none';
    var wrapper = fieldWrapper(modelField);
    if (wrapper) { wrapper.appendChild(box); }
    return box;
  }

  function updateModelOptions() {
    var partition = findField('gpu_partition');
    var model = findField('model_path');
    if (!partition || !model || !model.options) { return false; }

    var firstAvailable = null;
    var selectedIsAvailable = false;
    for (var i = 0; i < model.options.length; i += 1) {
      var option = model.options[i];
      var optionPartition = option.getAttribute('data-partition');
      var matches = !optionPartition || optionPartition === partition.value;
      var placeholder = option.getAttribute('data-model-placeholder') === 'true';

      option.hidden = !matches;
      option.style.display = matches ? '' : 'none';
      option.disabled = !matches || placeholder;

      if (matches && !placeholder) {
        if (!firstAvailable) { firstAvailable = option; }
        if (option.selected) { selectedIsAvailable = true; }
      }
    }

    if (!selectedIsAvailable) {
      if (firstAvailable) {
        firstAvailable.selected = true;
        model.value = firstAvailable.value;
      } else {
        model.selectedIndex = -1;
      }
    }

    var note = modelNoteBox(model);
    if (firstAvailable) {
      model.setCustomValidity('');
      if (note) { note.style.display = 'none'; }
    } else {
      model.setCustomValidity('No configured model is available for this partition.');
      if (note) {
        note.textContent = 'No models are configured for ' + partition.value +
          ' yet. Choose another partition or contact the application administrator.';
        note.style.display = '';
      }
    }

    return true;
  }

  function bindModelOptions() {
    var partition = findField('gpu_partition');
    if (!partition || partition.getAttribute('data-llama-model-bound') === 'true') {
      return;
    }

    partition.setAttribute('data-llama-model-bound', 'true');
    partition.addEventListener('change', updateModelOptions);
  }

  function noteBox() {
    var box = document.getElementById(NOTE_ID);
    if (box) { return box; }
    var field = findField('bc_num_hours');
    if (!field) { return null; }

    box = document.createElement('div');
    box.id = NOTE_ID;
    box.className = 'alert alert-info mt-2';
    // place directly under the hours field (inside its wrapper)
    var wrapper = fieldWrapper(field);
    if (wrapper) { wrapper.appendChild(box); }
    return box;
  }

  function update() {
    var field = findField('bc_num_hours');
    var sel = findField('gpu_partition');
    if (!field || !sel) { return false; }
    var box = noteBox();
    if (!box) { return false; }

    var hours = parseFloat(field.value);
    if (isNaN(hours) || hours < 0) { hours = 0; }
    var rate = RATES[sel.value];
    if (rate === undefined) {
      box.textContent = 'This job is estimated to cost up to $0.00 if run to completion.';
      return true;
    }

    var cost = GPUS * hours * rate;
    box.textContent = 'This job is estimated to cost up to $' +
      cost.toFixed(2) + ' if run to completion.';
    return true;
  }

  function bind() {
    var field = findField('bc_num_hours');
    var sel = findField('gpu_partition');
    if (field && field.getAttribute('data-llama-cost-bound') !== 'true') {
      field.setAttribute('data-llama-cost-bound', 'true');
      field.addEventListener('input', update);
      field.addEventListener('change', update);
    }
    if (sel && sel.getAttribute('data-llama-cost-bound') !== 'true') {
      sel.setAttribute('data-llama-cost-bound', 'true');
      sel.addEventListener('change', update);
    }
  }

  // The inline script may run before/while the page settles, so retry
  // for a few seconds until both fields exist and the note is rendered.
  var attempts = 0;
  var timer = setInterval(function () {
    attempts += 1;
    var costReady = update();
    var advancedReady = updateAdvancedVisibility();
    var modelsReady = updateModelOptions();
    bind();
    bindAdvanced();
    bindModelOptions();
    layoutMountRows();

    if (costReady && advancedReady && modelsReady) {
      clearInterval(timer);
    } else if (attempts > 40) { // ~10s give up
      clearInterval(timer);
    }
  }, 250);
})();
