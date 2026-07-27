document$.subscribe(() => {
  document.querySelectorAll("table").forEach((table) => {
    table.setAttribute("role", "table");
  });
});
