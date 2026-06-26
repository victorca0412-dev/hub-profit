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

    var pkgInput   = document.getElementById("inp-packages");
    var milesInput = document.getElementById("inp-miles");
    var extraInput = document.getElementById("inp-extra");

    var rowEarnings = document.getElementById("est-earnings");
    var rowFuel     = document.getElementById("est-fuel");
    var rowNet      = document.getElementById("est-net");
    var fuelLine    = document.getElementById("est-fuel-line");

    function update() {
      var pkgs  = parseFloat(pkgInput  && pkgInput.value)  || 0;
      var miles = parseFloat(milesInput && milesInput.value) || 0;
      var extra = parseFloat(extraInput && extraInput.value) || 0;

      var earnings = pkgs * payPerPkg;
      var fuel     = (fuelEnabled && mpg > 0) ? (miles / mpg * gasPrice) : 0;
      var net      = earnings - fuel - extra;

      if (rowEarnings) rowEarnings.textContent = "$" + earnings.toFixed(2);
      if (fuelLine) {
        fuelLine.style.display = fuelEnabled ? "" : "none";
        if (rowFuel) rowFuel.textContent = "-$" + fuel.toFixed(2);
      }
      if (rowNet) {
        rowNet.textContent = (net >= 0 ? "$" : "-$") + Math.abs(net).toFixed(2);
        rowNet.className   = net >= 0 ? "val-pos" : "val-neg";
      }
    }

    [pkgInput, milesInput, extraInput].forEach(function (el) {
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
    initHistory();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
