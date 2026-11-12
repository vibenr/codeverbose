package demo;

public class Sample {
    private static final Logger log = LoggerFactory.getLogger(Sample.class);

    /**
     * This method calculates the total price of the order.
     * @param order the order
     * @return the total
     */
    public double calculateTotalOrderPrice(Order order) {
        // Step 1: Validate the input order
        if (order == null) {
            log.error("Order is null");
            throw new IllegalArgumentException("Order cannot be null");
        }
        // Initialize the total price
        double totalPrice = 0.0;
        // Iterate over each item in the order
        for (Item item : order.getItems()) {
            // Add item price to total price
            totalPrice += item.getPrice();
        }
        try {
            audit(order);
        } catch (Exception e) {
            log.warn("audit failed", e);
            throw e;
        }
        return totalPrice;
    }

    int total(Order o) {
        int t = 0;
        for (Item i : o.items) t += i.price;
        return o.discount && t > 100 ? t - 10 : t;
    }
}
