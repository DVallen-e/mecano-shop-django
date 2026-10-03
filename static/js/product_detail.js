document.querySelectorAll("[data-product-purchase]").forEach((form) => {
    const quantityInput = form.querySelector("[data-quantity-input]");
    const details = form.closest(".product-details");
    const price = details.querySelector("[data-product-price]");
    const priceAmount = price.querySelector("[data-price-amount]");
    const totalLabel = details.querySelector("[data-product-total]");
    const unitPrice = Number(price.dataset.productPrice);

    if (!quantityInput || !Number.isFinite(unitPrice)) {
        return;
    }

    const updatePrice = () => {
        const quantity = Math.max(1, Number.parseInt(quantityInput.value, 10) || 1);
        const total = unitPrice * quantity;

        priceAmount.textContent = new Intl.NumberFormat("fr-FR", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        }).format(total);
        totalLabel.textContent = `Total pour ${quantity} article${quantity === 1 ? "" : "s"}`;
    };

    quantityInput.addEventListener("input", updatePrice);
    form.querySelectorAll("[data-quantity-step]").forEach((button) => {
        button.addEventListener("click", () => {
            const step = Number(button.dataset.quantityStep);
            const currentQuantity = Number.parseInt(quantityInput.value, 10) || 1;
            quantityInput.value = String(Math.max(1, currentQuantity + step));
            updatePrice();
        });
    });

    updatePrice();
});
