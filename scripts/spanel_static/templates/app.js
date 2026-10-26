/* JavaScript mínimo para comprobar que los archivos se sirven de verdad. */
(() => {
  const count = document.getElementById("count");
  const button = document.getElementById("counter");
  const host = document.getElementById("host");
  const year = document.getElementById("year");

  let clicks = 0;
  button?.addEventListener("click", () => {
    clicks += 1;
    if (count) count.textContent = String(clicks);
    button.animate(
      [{ transform: "scale(1)" }, { transform: "scale(0.97)" }, { transform: "scale(1)" }],
      { duration: 160, easing: "ease-out" }
    );
  });

  if (host) host.textContent = window.location.host || "localhost";
  if (year) year.textContent = String(new Date().getFullYear());

  document.documentElement.dataset.spanelStatic = "ready";
  console.info("SPanel static: HTML, CSS y JS cargados");
})();
