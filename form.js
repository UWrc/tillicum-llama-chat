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

  function findField(name) {
    return document.getElementById(name) ||
           document.querySelector('[name="' + name + '"]');
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
    var wrapper = (field.closest && field.closest('.form-group')) ||
                  field.parentElement ||
                  field.parentNode;
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
    if (field) {
      field.addEventListener('input', update);
      field.addEventListener('change', update);
    }
    if (sel) {
      sel.addEventListener('change', update);
    }
  }

  // The inline script may run before/while the page settles, so retry
  // for a few seconds until both fields exist and the note is rendered.
  var attempts = 0;
  var timer = setInterval(function () {
    attempts += 1;
    var ok = update();
    if (ok) {
      clearInterval(timer);
      bind();
    } else if (attempts > 40) { // ~10s give up
      clearInterval(timer);
    }
  }, 250);
})();
