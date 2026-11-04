-- Тестовый дамп для schema-visualizer
CREATE TABLE Address (
    address_id INT AUTO_INCREMENT PRIMARY KEY,
    city VARCHAR(100) NOT NULL,
    street VARCHAR(200),
    state VARCHAR(100)
);

CREATE TABLE Brand (
    brand_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    address_id INT,
    FOREIGN KEY (address_id) REFERENCES Address(address_id)
);

CREATE TABLE Model (
    model_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    brand_id INT,
    KEY idx_brand (brand_id),
    FOREIGN KEY (brand_id) REFERENCES Brand(brand_id)
);

CREATE TABLE Spaceship (
    spaceship_id INT AUTO_INCREMENT PRIMARY KEY,
    vin VARCHAR(50) UNIQUE,
    model_id INT,
    KEY idx_model (model_id),
    FOREIGN KEY (model_id) REFERENCES Model(model_id)
);

CREATE TABLE Currency (
    code CHAR(3) PRIMARY KEY,
    name VARCHAR(50) NOT NULL
);

CREATE TABLE Customer (
    customer_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    email VARCHAR(200) UNIQUE,
    address_id INT,
    KEY idx_address (address_id),
    FOREIGN KEY (address_id) REFERENCES Address(address_id)
);

CREATE TABLE Dealer (
    dealer_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    address_id INT,
    KEY idx_address (address_id),
    FOREIGN KEY (address_id) REFERENCES Address(address_id)
);

CREATE TABLE DealerCustomerRelation (
    id INT AUTO_INCREMENT PRIMARY KEY,
    dealer_id INT NOT NULL,
    customer_id INT NOT NULL,
    KEY idx_dealer (dealer_id),
    KEY idx_customer (customer_id),
    FOREIGN KEY (dealer_id) REFERENCES Dealer(dealer_id),
    FOREIGN KEY (customer_id) REFERENCES Customer(customer_id)
);

CREATE TABLE Sales (
    sale_id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    dealer_id INT NOT NULL,
    spaceship_id INT NOT NULL,
    sale_date DATE NOT NULL DEFAULT (CURRENT_DATE),
    status VARCHAR(20) NOT NULL DEFAULT 'new',
    amount DECIMAL(12,2),
    KEY idx_customer (customer_id),
    KEY idx_customer_date (customer_id, sale_date),
    KEY idx_status (status),
    KEY idx_dealer (dealer_id),
    KEY idx_ship (spaceship_id),
    FOREIGN KEY (customer_id) REFERENCES Customer(customer_id),
    FOREIGN KEY (dealer_id) REFERENCES Dealer(dealer_id),
    FOREIGN KEY (spaceship_id) REFERENCES Spaceship(spaceship_id)
);
