/* HubProfit — app.js
   Sections:
     1. Dashboard chart
     2. Log Day live estimate
     3. Settings — vehicle picker + MPG lookup + save confirm
*/

(function () {
  "use strict";

  /* ─────────────────────────────────────────
     1.  DASHBOARD CHART
     ───────────────────────────────────────── */
  function initDashboardChart() {
    var dataEl = document.getElementById("chartdata");
    var canvas = document.getElementById("netchart");
    if (!dataEl || !canvas) return;

    var byDay;
    try {
      byDay = JSON.parse(dataEl.textContent || dataEl.innerHTML);
    } catch (e) {
      return;
    }
    if (!byDay || !byDay.length) {
      canvas.parentElement.style.display = "none";
      return;
    }

    var labels = byDay.map(function (d) { return d.date; });
    var data   = byDay.map(function (d) { return d.net; });
    var colors = data.map(function (v) {
      return v >= 0 ? "rgba(37,99,235,0.85)" : "rgba(220,38,38,0.8)";
    });

    /* global Chart — loaded via chart.min.js */
    new Chart(canvas, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [{
          label: "Net Profit ($)",
          data: data,
          backgroundColor: colors,
          borderRadius: 5,
          borderSkipped: false,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                return " $" + ctx.parsed.y.toFixed(2);
              }
            }
          }
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { color: "#94a3b8", font: { size: 11 } }
          },
          y: {
            grid: { color: "#f1f5f9" },
            ticks: {
              color: "#94a3b8",
              font: { size: 11 },
              callback: function (v) { return (v < 0 ? "-$" : "$") + Math.abs(v); }
            }
          }
        }
      }
    });
  }

  /* ─────────────────────────────────────────
     2.  LOG DAY — LIVE ESTIMATE
     ───────────────────────────────────────── */
  function initLogEstimate() {
    var cfg = document.getElementById("log-config");
    if (!cfg) return;

    var payPerPkg    = parseFloat(cfg.dataset.payPerPackage)  || 0;
    var mpg          = parseFloat(cfg.dataset.vehicleMpg)     || 0;
    var gasPrice     = parseFloat(cfg.dataset.gasPrice)       || 0;
    var fuelEnabled  = cfg.dataset.fuelEnabled === "1";
    var rateModel    = cfg.dataset.rateModel || "flat";
    var tiers        = [];
    try { tiers = JSON.parse(cfg.dataset.tiers || "[]"); } catch (e) { tiers = []; }

    /* Whole-block lookup, mirroring calculations._rate_from_tiers: the
       day's count picks ONE tier and every package pays that rate. */
    function rateFor(pkgs) {
      if (rateModel !== "tiered") return payPerPkg;
      for (var i = 0; i < tiers.length; i++) {
        var lo = tiers[i].min_packages;
        var hi = tiers[i].max_packages;
        if (pkgs >= lo && (hi === null || pkgs <= hi)) return tiers[i].rate;
      }
      return payPerPkg;   // mirrors the server's fallback
    }

    var pkgInput   = document.getElementById("inp-packages");
    var milesInput = document.getElementById("inp-miles");
    var extraInput = document.getElementById("inp-extra");
    var driverSel  = document.getElementById("inp-driver");
    var milesField = milesInput ? milesInput.closest(".field") : null;

    var driverRates = {};
    try { driverRates = JSON.parse(cfg.dataset.driverRates || "{}"); }
    catch (e) { driverRates = {}; }

    function currentDriver() {
      if (!driverSel || !driverSel.value) return null;
      return driverRates[driverSel.value] || null;
    }

    var rowEarnings = document.getElementById("est-earnings");
    var rowFuel     = document.getElementById("est-fuel");
    var rowNet      = document.getElementById("est-net");
    var fuelLine    = document.getElementById("est-fuel-line");

    function update() {
      var pkgs  = parseFloat(pkgInput  && pkgInput.value)  || 0;
      var miles = parseFloat(milesInput && milesInput.value) || 0;
      var extra = parseFloat(extraInput && extraInput.value) || 0;

      var driver = currentDriver();
      /* The driver runs their own vehicle, so their mileage is not the
         owner's cost - hide the field and drop it from the estimate. */
      if (milesField) milesField.style.display = driver ? "none" : "";
      if (driver) miles = 0;

      var rate      = rateFor(pkgs);
      var earnings  = pkgs * rate;
      var fuel      = (fuelEnabled && mpg > 0) ? (miles / mpg * gasPrice) : 0;
      var driverPay = 0;
      if (driver) {
        driverPay = driver.model === "per_day"
          ? driver.rate : pkgs * driver.rate;
      }
      var net = earnings - fuel - extra - driverPay;

      if (rowEarnings) rowEarnings.textContent = "$" + earnings.toFixed(2);
      var rateNote = document.getElementById("est-rate-note");
      if (rateNote) {
        rateNote.textContent = pkgs
          ? pkgs + " × $" + rate.toFixed(2)
          : "";
      }
      if (fuelLine) {
        /* Hidden entirely on driver days: they run their own vehicle, so
           showing them a -$0.00 fuel line is noise. */
        fuelLine.style.display = (fuelEnabled && !driver) ? "" : "none";
        if (rowFuel) rowFuel.textContent = "-$" + fuel.toFixed(2);
      }
      var driverLine = document.getElementById("est-driver-line");
      var driverCell = document.getElementById("est-driver");
      var noteLine   = document.getElementById("est-margin-note-line");
      var noteCell   = document.getElementById("est-margin-note");
      var netLabel   = document.getElementById("est-net-label");

      if (driverLine) driverLine.hidden = !driver;
      if (driver && driverCell) {
        driverCell.textContent = "-$" + driverPay.toFixed(2);
      }
      if (netLabel) {
        netLabel.textContent = driver
          ? "Your margin" : "Net (before fixed costs)";
      }
      if (noteLine && noteCell) {
        if (driver && pkgs > 0) {
          noteLine.hidden = false;
          noteCell.textContent = net < 0
            ? "This block loses money at " + driver.name + "'s rate"
            : "$" + (net / pkgs).toFixed(2) + " per package";
          noteCell.className = net < 0 ? "val-neg" : "text-muted";
        } else {
          noteLine.hidden = true;
        }
      }

      if (rowNet) {
        rowNet.textContent = (net >= 0 ? "$" : "-$") + Math.abs(net).toFixed(2);
        rowNet.className   = net >= 0 ? "val-pos" : "val-neg";
      }
    }

    [pkgInput, milesInput, extraInput, driverSel].forEach(function (el) {
      if (el) {
        el.addEventListener("input", update);
        el.addEventListener("change", update);
      }
    });
    update();
  }

  /* ─────────────────────────────────────────
     3.  SETTINGS — vehicle picker + MPG + confirm
     ───────────────────────────────────────── */
  function initSettings() {
    var yearSel  = document.getElementById("vehicle_year");
    var makeSel  = document.getElementById("vehicle_make");
    var modelSel = document.getElementById("vehicle_model");
    var mpgInput = document.getElementById("vehicle_mpg");
    var lookupBtn = document.getElementById("mpg-lookup-btn");
    var settingsForm = document.getElementById("settings-form");

    /* ── Populate year dropdown ── */
    if (yearSel) {
      var currentYear = new Date().getFullYear();
      var savedYear   = yearSel.dataset.saved || "";
      yearSel.innerHTML = '<option value="">Select year</option>';
      for (var y = currentYear + 1; y >= 1985; y--) {
        var opt = document.createElement("option");
        opt.value = String(y);
        opt.textContent = String(y);
        if (String(y) === savedYear) opt.selected = true;
        yearSel.appendChild(opt);
      }

      yearSel.addEventListener("change", function () {
        makeSel.innerHTML = '<option value="">Loading…</option>';
        makeSel.disabled  = true;
        modelSel.innerHTML = '<option value="">Select model</option>';
        modelSel.disabled  = true;
        var yr = yearSel.value;
        if (!yr) {
          makeSel.innerHTML = '<option value="">Select make</option>';
          return;
        }
        fetch("/api/makes?year=" + encodeURIComponent(yr))
          .then(function (r) { return r.json(); })
          .then(function (makes) {
            makeSel.innerHTML = '<option value="">Select make</option>';
            makes.forEach(function (m) {
              var o = document.createElement("option");
              o.value = m; o.textContent = m;
              makeSel.appendChild(o);
            });
            makeSel.disabled = false;
          })
          .catch(function () {
            makeSel.innerHTML = '<option value="">Error loading makes</option>';
            makeSel.disabled  = false;
          });
      });
    }

    /* ── Populate make dropdown ── */
    if (makeSel) {
      makeSel.addEventListener("change", function () {
        modelSel.innerHTML = '<option value="">Loading…</option>';
        modelSel.disabled  = true;
        var yr   = yearSel  ? yearSel.value  : "";
        var make = makeSel.value;
        if (!yr || !make) {
          modelSel.innerHTML = '<option value="">Select model</option>';
          modelSel.disabled  = false;
          return;
        }
        fetch("/api/models?year=" + encodeURIComponent(yr) + "&make=" + encodeURIComponent(make))
          .then(function (r) { return r.json(); })
          .then(function (models) {
            modelSel.innerHTML = '<option value="">Select model</option>';
            models.forEach(function (m) {
              var o = document.createElement("option");
              o.value = m; o.textContent = m;
              modelSel.appendChild(o);
            });
            modelSel.disabled = false;
          })
          .catch(function () {
            modelSel.innerHTML = '<option value="">Error loading models</option>';
            modelSel.disabled  = false;
          });
      });
    }

    /* ── Pre-load saved make / model on page load ── */
    function preselectSaved() {
      if (!yearSel || !makeSel || !modelSel) return;
      var savedMake  = makeSel.dataset.saved  || "";
      var savedModel = modelSel.dataset.saved || "";
      if (!savedMake || !yearSel.value) return;

      fetch("/api/makes?year=" + encodeURIComponent(yearSel.value))
        .then(function (r) { return r.json(); })
        .then(function (makes) {
          makeSel.innerHTML = '<option value="">Select make</option>';
          makes.forEach(function (m) {
            var o = document.createElement("option");
            o.value = m; o.textContent = m;
            if (m === savedMake) o.selected = true;
            makeSel.appendChild(o);
          });
          makeSel.disabled = false;

          if (!savedModel) return;
          return fetch("/api/models?year=" + encodeURIComponent(yearSel.value) +
            "&make=" + encodeURIComponent(savedMake))
            .then(function (r) { return r.json(); })
            .then(function (models) {
              modelSel.innerHTML = '<option value="">Select model</option>';
              models.forEach(function (m) {
                var o = document.createElement("option");
                o.value = m; o.textContent = m;
                if (m === savedModel) o.selected = true;
                modelSel.appendChild(o);
              });
              modelSel.disabled = false;
            });
        })
        .catch(function () {
          // API unreachable: re-enable the make select so saved vehicle
          // fields are not dropped from a subsequent settings save.
          makeSel.innerHTML = '<option value="">Select make</option>';
          makeSel.disabled = false;
        });
    }
    preselectSaved();

    /* ── MPG Lookup button ── */
    if (lookupBtn) {
      lookupBtn.addEventListener("click", function () {
        var yr    = yearSel  ? yearSel.value  : "";
        var make  = makeSel  ? makeSel.value  : "";
        var model = modelSel ? modelSel.value : "";
        if (!yr || !make || !model) {
          alert("Please select Year, Make, and Model first.");
          return;
        }
        lookupBtn.disabled    = true;
        lookupBtn.textContent = "Looking up…";
        var fd = new FormData();
        fd.append("year",  yr);
        fd.append("make",  make);
        fd.append("model", model);
        fetch("/api/lookup_mpg", { method: "POST", body: fd })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.mpg && mpgInput) {
              mpgInput.value = data.mpg;
              lookupBtn.textContent = "MPG updated!";
              setTimeout(function () {
                lookupBtn.textContent = "Look up MPG";
                lookupBtn.disabled    = false;
              }, 2000);
            } else {
              lookupBtn.textContent = "Not found";
              lookupBtn.disabled    = false;
            }
          })
          .catch(function () {
            lookupBtn.textContent = "Error";
            lookupBtn.disabled    = false;
          });
      });
    }

    /* ── Save confirmation ── */
    if (settingsForm) {
      settingsForm.addEventListener("submit", function (e) {
        var ok = confirm(
          "Changing rate or cost settings applies to FUTURE entries only. " +
          "Days already logged keep the rate and costs they were saved with. Continue?"
        );
        if (!ok) e.preventDefault();
      });
    }
  }

  /* ─────────────────────────────────────────
     SETTINGS — fluctuating contract tier editor
     ───────────────────────────────────────── */
  function initTierEditor() {
    var editor = document.getElementById("tier-editor");
    var tbody  = document.getElementById("tier-rows");
    var addBtn = document.getElementById("tier-add");
    if (!editor || !tbody) return;

    var radios = document.querySelectorAll('input[name="rate_model"]');

    function toggleEditor() {
      var tiered = document.querySelector('input[name="rate_model"]:checked');
      editor.hidden = !(tiered && tiered.value === "tiered");
      /* Choosing Fluctuating with no tiers yet would leave an empty table
         and the save would fail validation, so seed a first row. */
      if (!editor.hidden && !tbody.querySelectorAll(".tier-row").length) {
        addRow();
      }
    }

    /* The "From" cells are derived, never submitted: the first tier starts
       at 1 and each next starts one above the previous ceiling. Recomputing
       them here is what makes a gap or an overlap impossible to express. */
    function renumber() {
      var rows = tbody.querySelectorAll(".tier-row");
      var low = 1;
      rows.forEach(function (row, i) {
        row.querySelector(".tier-from").textContent = low;
        var to = row.querySelector('input[name="tier_to"]');
        to.placeholder = (i === rows.length - 1) ? "∞" : "e.g. 40";
        var value = parseInt(to.value, 10);
        if (!isNaN(value)) low = value + 1;
      });
    }

    function addRow() {
      var row = document.createElement("tr");
      row.className = "tier-row";
      row.innerHTML =
        '<td class="tier-from"></td>' +
        '<td><input type="number" name="tier_to" min="1" step="1"></td>' +
        '<td>$ <input type="number" name="tier_rate" min="0" step="0.01" required></td>' +
        '<td><button type="button" class="btn btn-sm btn-ghost tier-remove">&times;</button></td>';
      tbody.appendChild(row);
      renumber();
      var previous = row.previousElementSibling;
      if (previous) {
        /* The row that was last had an open-ended ceiling. Now that it is
           not last, it needs one - send the user straight there. */
        var previousTo = previous.querySelector('input[name="tier_to"]');
        if (!previousTo.value) { previousTo.focus(); return; }
      }
      row.querySelector('input[name="tier_rate"]').focus();
    }

    radios.forEach(function (r) { r.addEventListener("change", toggleEditor); });
    if (addBtn) addBtn.addEventListener("click", addRow);

    tbody.addEventListener("click", function (e) {
      if (!e.target.classList.contains("tier-remove")) return;
      var rows = tbody.querySelectorAll(".tier-row");
      if (rows.length <= 1) return;   // always keep at least one tier
      e.target.closest(".tier-row").remove();
      renumber();
    });

    tbody.addEventListener("input", function (e) {
      if (e.target.name === "tier_to") renumber();
    });

    renumber();
  }

  /* ─────────────────────────────────────────
     HELP — manual update check
     ───────────────────────────────────────── */
  function initUpdateCheck() {
    var btn = document.getElementById("update-check-btn");
    var out = document.getElementById("update-check-result");
    if (!btn || !out) return;

    btn.addEventListener("click", function () {
      btn.disabled = true;
      out.textContent = "Checking…";
      out.className = "hint";
      fetch("/api/update-check")
        .then(function (r) { return r.json(); })
        .then(function (d) {
          out.textContent = d.message || "";
          if (d.status === "update-available") {
            out.className = "hint val-neg";
            var a = document.createElement("a");
            a.href = d.releases_url;
            a.target = "_blank";
            a.rel = "noopener";
            a.textContent = " Download";
            out.appendChild(a);
          } else {
            out.className = "hint";
          }
        })
        .catch(function () {
          /* The server already turns failures into a message, so this
             only fires if the app itself is unreachable. */
          out.textContent = "Could not check right now.";
        })
        .finally(function () { btn.disabled = false; });
    });
  }

  /* ─────────────────────────────────────────
     HISTORY — delete confirm
     ───────────────────────────────────────── */
  function initHistory() {
    document.querySelectorAll(".delete-day-form").forEach(function (form) {
      form.addEventListener("submit", function (e) {
        if (!confirm("Delete this day?")) e.preventDefault();
      });
    });
  }

  /* ─────────────────────────────────────────
     BOOT
     ───────────────────────────────────────── */
  function boot() {
    initDashboardChart();
    initLogEstimate();
    initSettings();
    initTierEditor();
    initUpdateCheck();
    initHistory();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
