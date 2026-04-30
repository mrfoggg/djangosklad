document.addEventListener("change", async (event) => {
	if (event.target && event.target.id.endsWith("-product")) {
		const productSelect = event.target;
		const productId = productSelect.value;

		// Для SalesOrder может быть id_customer, для PurchaseOrder - id_supplier.
		// Можно искать универсально через селектор, который есть в шапке документа.
		const partnerSelect = document.getElementById("id_supplier") || document.getElementById("id_customer");
		const partnerId = partnerSelect ? partnerSelect.value : null;

		const row = productSelect.closest("tr, .inline-related");

		if (partnerId && productId) {
			const url = new URL("/documents/get-price/", window.location.origin);
			url.searchParams.append("supplier_id", partnerId); // Бэкенд пока ждет supplier_id
			url.searchParams.append("product_id", productId);

			try {
				const response = await fetch(url);
				const data = await response.json();
				const isDark = document.documentElement.classList.contains("dark");

				// Теперь поле цены называется просто ...-price, так как это DecimalField
				const priceInput = row.querySelector('input[id$="-price"]');

				if (priceInput) {
					priceInput.value = data.price;

					// Подсветка для обратной связи
					const colorClass = data.status === "success" ? "bg-green-100" : data.status === "error" ? "bg-red-100" : "bg-yellow-100";
					const darkColorClass =
						data.status === "success" ? "dark:bg-green-900/30" : data.status === "error" ? "dark:bg-red-900/30" : "dark:bg-yellow-900/30";

					priceInput.classList.add(colorClass, darkColorClass);
					setTimeout(() => priceInput.classList.remove(colorClass, darkColorClass), 1000);

					// Триггерим событие изменения, если у тебя есть скрипты для пересчета итоговой суммы строки
					priceInput.dispatchEvent(new Event("change", { bubbles: true }));
				}

				// Выводим алерт SweetAlert2
				Swal.fire({
					icon: data.status,
					title: data.title,
					html: data.message,
					toast: true,
					position: "top-end",
					showConfirmButton: false,
					timer: 4000,
					timerProgressBar: true,
					background: isDark ? "#1f2937" : "#fff",
					color: isDark ? "#fff" : "#000",
				});
			} catch (error) {
				console.error("Fetch error:", error);
			}
		}
	}
});
