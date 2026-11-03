# InsightX accuracy test

Run started 20261103-175139. Models that answered (share of AI calls): gemini/gemini-3.5-flash-lite (41%), groq/openai/gpt-oss-120b (21%), groq/qwen/qwen3.8-27b (20%), openrouter/nvidia/nemotron-3-ultra-550b-a55b:free (10%), groq/openai/gpt-oss-20b (6%), gemini/gemini-3.6-flash (2%).

Execution accuracy: InsightX's SQL and the gold SQL are both run, and the result rows are compared.
**Strict** = same columns and rows (official Spider rule). **Lenient** = expected columns and rows all present, extra columns allowed.

## Overall

| Test set | Questions | Strict | Lenient | Lenient 95% CI | AI-service errors |
|---|---|---|---|---|---|
| Spider dev (unseen databases) | 104 | 79.8% (83) | **85.6%** (89) | 77.6% to 91.1% | 1 |
| Own gold set (shop.sqlite) | 24 | 37.5% (9) | **50.0%** (12) | 31.4% to 68.6% | 11 |
| All | 128 | 71.9% (92) | **78.9%** (101) | 71.0% to 85.1% | 12 |

## Spider dev (unseen databases): by difficulty

| Difficulty | Questions | Strict | Lenient | Lenient 95% CI | AI-service errors |
|---|---|---|---|---|---|
| easy | 18 | 83.3% (15) | **83.3%** (15) | 60.8% to 94.2% | 0 |
| medium | 52 | 90.4% (47) | **92.3%** (48) | 81.8% to 97.0% | 0 |
| hard | 17 | 76.5% (13) | **88.2%** (15) | 65.7% to 96.7% | 1 |
| extra | 17 | 47.1% (8) | **64.7%** (11) | 41.3% to 82.7% | 0 |

## Spider dev (unseen databases): by database

| Database | Questions | Strict | Lenient | Lenient 95% CI | AI-service errors |
|---|---|---|---|---|---|
| spider_car_1 | 13 | 76.9% (10) | **84.6%** (11) | 57.8% to 95.7% | 0 |
| spider_concert_singer | 13 | 84.6% (11) | **92.3%** (12) | 66.7% to 98.6% | 0 |
| spider_dog_kennels | 13 | 76.9% (10) | **76.9%** (10) | 49.7% to 91.8% | 0 |
| spider_employee_hire_evaluation | 13 | 100.0% (13) | **100.0%** (13) | 77.2% to 100.0% | 0 |
| spider_flight_2 | 13 | 69.2% (9) | **76.9%** (10) | 49.7% to 91.8% | 0 |
| spider_pets_1 | 13 | 100.0% (13) | **100.0%** (13) | 77.2% to 100.0% | 0 |
| spider_student_transcripts_tracking | 13 | 61.5% (8) | **61.5%** (8) | 35.5% to 82.3% | 0 |
| spider_world_1 | 13 | 69.2% (9) | **92.3%** (12) | 66.7% to 98.6% | 1 |

## Own gold set (shop.sqlite): by difficulty

| Difficulty | Questions | Strict | Lenient | Lenient 95% CI | AI-service errors |
|---|---|---|---|---|---|
| easy | 7 | 71.4% (5) | **71.4%** (5) | 35.9% to 91.8% | 2 |
| medium | 7 | 28.6% (2) | **57.1%** (4) | 25.0% to 84.2% | 3 |
| hard | 5 | 20.0% (1) | **40.0%** (2) | 11.8% to 76.9% | 3 |
| extra | 5 | 20.0% (1) | **20.0%** (1) | 3.6% to 62.4% | 3 |

## Own gold set (shop.sqlite): by database

| Database | Questions | Strict | Lenient | Lenient 95% CI | AI-service errors |
|---|---|---|---|---|---|
| own_shop | 24 | 37.5% (9) | **50.0%** (12) | 31.4% to 68.6% | 11 |

## Why questions failed

| Cause | Count |
|---|---|
| pipeline_error | 12 |
| wrong_filter | 5 |
| wrong_calculation | 2 |
| wrong_join | 2 |
| wrong_aggregation | 2 |
| clarification | 1 |
| wrong_grouping | 1 |
| wrong_order | 1 |
| missing_table | 1 |

## Every failed question

### car_1_0151 (spider_car_1, extra)

- Question: Which distinctive models are produced by maker with the full name General Motors or weighing more than 3500?
- Status: NEEDS_CLARIFICATION; cause: clarification
- Detail: agent status NEEDS_CLARIFICATION

Gold SQL:
```sql
SELECT DISTINCT T2.Model FROM CAR_NAMES AS T1 JOIN MODEL_LIST AS T2 ON T1.Model  =  T2.Model JOIN CAR_MAKERS AS T3 ON T2.Maker  =  T3.Id JOIN CARS_DATA AS T4 ON T1.MakeId  =  T4.Id WHERE T3.FullName  =  'General Motors' OR T4.weight  >  3500
```
InsightX SQL:
```sql
(none)
```

### car_1_0161 (spider_car_1, medium)

- Question: How many countries has more than 2 car makers ?
- Status: SUCCESS; cause: wrong_filter
- Detail: filtered on id, expected no filter

Gold SQL:
```sql
select count(*) from countries as t1 join car_makers as t2 on t1.countryid  =  t2.country group by t1.countryid having count(*)  >  2
```
InsightX SQL:
```sql
SELECT COUNT(*) FROM (
    SELECT cm.Country
    FROM car_makers cm
    JOIN countries c ON cm.Country = c.CountryId
    GROUP BY cm.Country
    HAVING COUNT(cm.Id) > 2
)
```

### concert_singer_0037 (spider_concert_singer, hard)

- Question: List all singer names in concerts in year 2014.
- Status: SUCCESS; cause: wrong_calculation
- Detail: expected name; got "name"

Gold SQL:
```sql
SELECT T2.name FROM singer_in_concert AS T1 JOIN singer AS T2 ON T1.singer_id  =  T2.singer_id JOIN concert AS T3 ON T1.concert_id  =  T3.concert_id WHERE T3.year  =  2014
```
InsightX SQL:
```sql
SELECT DISTINCT s."Name"
FROM "singer" s
JOIN "singer_in_concert" sic ON s."Singer_ID" = sic."Singer_ID"
JOIN "concert" c ON sic."concert_ID" = c."concert_ID"
WHERE c."Year" = '2014';
```

### dog_kennels_0950 (spider_dog_kennels, medium)

- Question: List each owner's first name, last name, and the size of his for her dog.
- Status: SUCCESS; cause: wrong_join
- Detail: extra: sizes

Gold SQL:
```sql
SELECT T1.first_name ,  T1.last_name ,  T2.size_code FROM Owners AS T1 JOIN Dogs AS T2 ON T1.owner_id  =  T2.owner_id
```
InsightX SQL:
```sql
SELECT
    o."first_name",
    o."last_name",
    s."size_description"
FROM "Owners" AS o
JOIN "Dogs" AS d ON o."owner_id" = d."owner_id"
JOIN "Sizes" AS s ON d."size_code" = s."size_code";
```

### dog_kennels_0954 (spider_dog_kennels, extra)

- Question: List the names of the dogs of the rarest breed and the treatment dates of them.
- Status: SUCCESS; cause: wrong_join
- Detail: extra: breeds

Gold SQL:
```sql
SELECT T1.name ,  T2.date_of_treatment FROM Dogs AS T1 JOIN Treatments AS T2 ON T1.dog_id  =  T2.dog_id WHERE T1.breed_code  =  ( SELECT breed_code FROM Dogs GROUP BY breed_code ORDER BY count(*) ASC LIMIT 1 )
```
InsightX SQL:
```sql
WITH breed_counts AS (
    SELECT d.breed_code,
           COUNT(*) AS dog_cnt
    FROM Dogs d
    JOIN Breeds b ON d.breed_code = b.breed_code
    GROUP BY d.breed_code
),
min_count AS (
    SELECT MIN(dog_cnt) AS min_cnt
    FROM breed_counts
),
rarest_breeds AS (
    SELECT breed_code
    FROM breed_counts
    WHERE dog_cnt = (SELECT min_cnt FROM min_count)
)
SELECT d.name AS dog_name,
       t.date_of_treatment AS treatment_date
FROM Dogs d
JOIN Treatments t ON t.dog_id = d.dog_id
WHERE d.breed_code IN (SELECT breed_code FROM rarest_breeds)
ORDER BY d.name, t.date_of_treatment;
```

### dog_kennels_0960 (spider_dog_kennels, extra)

- Question: List the last name of the owner owning the youngest dog.
- Status: SUCCESS; cause: wrong_aggregation
- Detail: expected MAX; got MIN

Gold SQL:
```sql
SELECT T1.last_name FROM Owners AS T1 JOIN Dogs AS T2 ON T1.owner_id  =  T2.owner_id WHERE T2.age  =  ( SELECT max(age) FROM Dogs )
```
InsightX SQL:
```sql
SELECT DISTINCT o.last_name
FROM Owners o
JOIN Dogs d ON d.owner_id = o.owner_id
WHERE CAST(d.age AS INTEGER) = (
    SELECT MIN(CAST(age AS INTEGER))
    FROM Dogs
    WHERE age IS NOT NULL
);
```

### flight_2_0203 (spider_flight_2, easy)

- Question: How many flights depart from 'APG'?
- Status: SUCCESS; cause: wrong_filter
- Detail: got APG

Gold SQL:
```sql
SELECT count(*) FROM FLIGHTS WHERE SourceAirport  =  "APG"
```
InsightX SQL:
```sql
SELECT COUNT(*) AS flight_count
FROM flights
WHERE SourceAirport = ' APG';
```

### flight_2_0217 (spider_flight_2, medium)

- Question: How many 'United Airlines' flights depart from Airport 'AHD'?
- Status: SUCCESS; cause: wrong_filter
- Detail: got AHD, United Airlines

Gold SQL:
```sql
SELECT count(*) FROM AIRLINES AS T1 JOIN FLIGHTS AS T2 ON T2.Airline  =  T1.uid WHERE T1.Airline  =  "United Airlines" AND T2.SourceAirport  =  "AHD"
```
InsightX SQL:
```sql
SELECT COUNT(*) AS flight_count
FROM flights
JOIN airlines ON flights.Airline = airlines.uid
WHERE airlines.Airline = 'United Airlines'
  AND flights.SourceAirport = ' AHD';
```

### flight_2_0231 (spider_flight_2, extra)

- Question: Find the abbreviation and country of the airline that has fewest number of flights?
- Status: SUCCESS; cause: wrong_grouping
- Detail: grouped by abbreviation, country, uid, expected airline

Gold SQL:
```sql
SELECT T1.Abbreviation ,  T1.Country FROM AIRLINES AS T1 JOIN FLIGHTS AS T2 ON T1.uid  =  T2.Airline GROUP BY T1.Airline ORDER BY count(*) LIMIT 1
```
InsightX SQL:
```sql
SELECT a.Abbreviation, a.Country
FROM airlines a
JOIN flights f ON a.uid = f.Airline
GROUP BY a.uid, a.Abbreviation, a.Country
ORDER BY COUNT(f.FlightNo) ASC
LIMIT 1
```

### student_transcripts_tracking_0533 (spider_student_transcripts_tracking, medium)

- Question: Who are enrolled in 2 degree programs in one semester? List the first name, middle name and last name and the id.
- Status: SUCCESS; cause: wrong_filter
- Detail: filtered on degree_program_id, expected no filter

Gold SQL:
```sql
SELECT T1.first_name ,  T1.middle_name ,  T1.last_name ,  T1.student_id FROM Students AS T1 JOIN Student_Enrolment AS T2 ON T1.student_id  =  T2.student_id GROUP BY T1.student_id HAVING count(*)  =  2
```
InsightX SQL:
```sql
WITH multi_enrolled AS (
    SELECT student_id
    FROM Student_Enrolment
    GROUP BY student_id, semester_id
    HAVING COUNT(DISTINCT degree_program_id) = 2
)
SELECT DISTINCT
    s.first_name,
    s.middle_name,
    s.last_name,
    s.student_id
FROM Students s
JOIN multi_enrolled me
    ON s.student_id = me.student_id;
```

### student_transcripts_tracking_0541 (spider_student_transcripts_tracking, extra)

- Question: Which student has enrolled for the most times in any program? List the id, first name, middle name, last name, the number of enrollments and student id.
- Status: SUCCESS; cause: wrong_aggregation
- Detail: expected COUNT; got MAX

Gold SQL:
```sql
SELECT T1.student_id ,  T1.first_name ,  T1.middle_name ,  T1.last_name ,  count(*) ,  T1.student_id FROM Students AS T1 JOIN Student_Enrolment AS T2 ON T1.student_id  =  T2.student_id GROUP BY T1.student_id ORDER BY count(*) DESC LIMIT 1
```
InsightX SQL:
```sql
WITH enrollment_counts AS (
    SELECT student_id, COUNT(student_enrolment_id) AS enrollment_count
    FROM Student_Enrolment
    GROUP BY student_id
),
max_enrollment AS (
    SELECT MAX(enrollment_count) AS max_count FROM enrollment_counts
)
SELECT s.student_id AS id,
       s.first_name,
       s.middle_name,
       s.last_name,
       ec.enrollment_count,
       s.student_id AS student_id
FROM Students s
JOIN enrollment_counts ec ON s.student_id = ec.student_id
JOIN max_enrollment me ON ec.enrollment_count = me.max_count;
```

### student_transcripts_tracking_0561 (spider_student_transcripts_tracking, extra)

- Question: Which address holds the most number of students currently? List the address id and all lines.
- Status: SUCCESS; cause: wrong_filter
- Detail: filtered on date_left, expected no filter

Gold SQL:
```sql
SELECT T1.address_id ,  T1.line_1 ,  T1.line_2 FROM Addresses AS T1 JOIN Students AS T2 ON T1.address_id  =  T2.current_address_id GROUP BY T1.address_id ORDER BY count(*) DESC LIMIT 1
```
InsightX SQL:
```sql
SELECT 
    a.address_id,
    a.line_1,
    a.line_2,
    a.line_3
FROM Addresses a
JOIN Students s ON a.address_id = s.current_address_id
WHERE s.date_left IS NULL
GROUP BY a.address_id, a.line_1, a.line_2, a.line_3
ORDER BY COUNT(s.student_id) DESC
LIMIT 1
```

### student_transcripts_tracking_0563 (spider_student_transcripts_tracking, easy)

- Question: On average, when were the transcripts printed?
- Status: SUCCESS; cause: wrong_calculation
- Detail: expected avg(transcript_date); got datetime(avg(julianday("transcript_date")))

Gold SQL:
```sql
SELECT avg(transcript_date) FROM Transcripts
```
InsightX SQL:
```sql
SELECT datetime(AVG(julianday("transcript_date"))) AS average_transcript_date
FROM "Transcripts";
```

### student_transcripts_tracking_0579 (spider_student_transcripts_tracking, easy)

- Question: List all the student details in reversed lexicographical order.
- Status: SUCCESS; cause: wrong_order
- Detail: same rows in a different order

Gold SQL:
```sql
SELECT other_student_details FROM Students ORDER BY other_student_details DESC
```
InsightX SQL:
```sql
SELECT
    student_id,
    current_address_id,
    permanent_address_id,
    first_name,
    middle_name,
    last_name,
    cell_mobile_number,
    email_address,
    ssn,
    date_first_registered,
    date_left,
    other_student_details
FROM
    Students
ORDER BY
    last_name DESC,
    first_name DESC,
    middle_name DESC;
```

### world_1_0814 (spider_world_1, hard)

- Question: Which language is spoken by the largest number of countries?
- Status: ERROR; cause: pipeline_error
- Detail: stage: generate_sql (All free AI models are busy or out of their free limits right now (Groq: quota; Google Gemini: quota; OpenRouter: not found). Try again in about 17 hours.)

Gold SQL:
```sql
SELECT LANGUAGE FROM countrylanguage GROUP BY LANGUAGE ORDER BY count(*) DESC LIMIT 1
```
InsightX SQL:
```sql
(none)
```

### dvd_revenue_per_store (own_shop, extra)

- Question: What is the total payment revenue for each store?
- Status: SUCCESS; cause: missing_table
- Detail: not used: dvd_staff

Gold SQL:
```sql
SELECT s.store_id, SUM(p.amount) FROM dvd_payment p
JOIN dvd_staff s ON s.staff_id = p.staff_id
GROUP BY s.store_id
```
InsightX SQL:
```sql
SELECT
    s.store_id,
    SUM(p.amount) AS total_payment_revenue
FROM dvd_store s
JOIN dvd_customer c ON s.store_id = c.store_id
JOIN dvd_payment p ON c.customer_id = p.customer_id
GROUP BY s.store_id
```

### music_top_genre (own_shop, medium)

- Question: Which music genre has the most tracks?
- Status: ERROR; cause: pipeline_error
- Detail: stage: generate_sql (All free AI models are busy or out of their free limits right now (Groq: quota (retry in 63245s); Google Gemini: quota (retry in 63245s); OpenRouter: not found )

Gold SQL:
```sql
SELECT g.name FROM music_genre g
JOIN music_track t ON t.genre_id = g.genre_id
GROUP BY g.genre_id, g.name ORDER BY COUNT(*) DESC LIMIT 1
```
InsightX SQL:
```sql
(none)
```

### music_top_artist_albums (own_shop, medium)

- Question: Which artist has released the most albums?
- Status: ERROR; cause: pipeline_error
- Detail: stage: generate_sql (All free AI models are busy or out of their free limits right now (Groq: quota (retry in 63118s); Google Gemini: quota (retry in 63118s); OpenRouter: not found )

Gold SQL:
```sql
SELECT ar.name FROM music_artist ar
JOIN music_album al ON al.artist_id = ar.artist_id
GROUP BY ar.artist_id, ar.name ORDER BY COUNT(*) DESC LIMIT 1
```
InsightX SQL:
```sql
(none)
```

### music_genre_revenue_top3 (own_shop, extra)

- Question: Which 3 genres earned the most money from track sales?
- Status: ERROR; cause: pipeline_error
- Detail: stage: generate_sql (All free AI models are busy or out of their free limits right now (Groq: quota (retry in 62984s); Google Gemini: quota (retry in 62984s); OpenRouter: not found )

Gold SQL:
```sql
SELECT g.name FROM music_invoice_line il
JOIN music_track t ON t.track_id = il.track_id
JOIN music_genre g ON g.genre_id = t.genre_id
GROUP BY g.genre_id, g.name ORDER BY SUM(il.unit_price * il.quantity) DESC LIMIT 3
```
InsightX SQL:
```sql
(none)
```

### dvd_avg_rate_by_rating (own_shop, medium)

- Question: What is the average rental rate for each film rating?
- Status: ERROR; cause: pipeline_error
- Detail: stage: generate_sql (All free AI models are busy or out of their free limits right now (Groq: quota (retry in 62854s); Google Gemini: quota (retry in 62854s); OpenRouter: not found )

Gold SQL:
```sql
SELECT rating, AVG(rental_rate) FROM dvd_film GROUP BY rating
```
InsightX SQL:
```sql
(none)
```

### dvd_unreturned (own_shop, easy)

- Question: How many rentals have not been returned yet?
- Status: ERROR; cause: pipeline_error
- Detail: stage: generate_sql (All free AI models are busy or out of their free limits right now (Groq: quota (retry in 62728s); Google Gemini: quota (retry in 62728s); OpenRouter: not found )

Gold SQL:
```sql
SELECT COUNT(*) FROM dvd_rental WHERE return_date IS NULL
```
InsightX SQL:
```sql
(none)
```

### dvd_top_actor (own_shop, hard)

- Question: Which actor has acted in the most films? Give first and last name.
- Status: ERROR; cause: pipeline_error
- Detail: All free AI models are busy or out of their free limits (daily quota; not asked)

Gold SQL:
```sql
SELECT a.first_name, a.last_name FROM dvd_actor a
JOIN dvd_film_actor fa ON fa.actor_id = a.actor_id
GROUP BY a.actor_id, a.first_name, a.last_name ORDER BY COUNT(*) DESC LIMIT 1
```
InsightX SQL:
```sql
(none)
```

### dvd_films_not_in_stock (own_shop, hard)

- Question: How many films have no copies in the inventory?
- Status: ERROR; cause: pipeline_error
- Detail: All free AI models are busy or out of their free limits (daily quota; not asked)

Gold SQL:
```sql
SELECT COUNT(*) FROM dvd_film
WHERE film_id NOT IN (SELECT film_id FROM dvd_inventory)
```
InsightX SQL:
```sql
(none)
```

### nw_top3_products_revenue (own_shop, extra)

- Question: What are the top 3 products by sales revenue after discount?
- Status: ERROR; cause: pipeline_error
- Detail: All free AI models are busy or out of their free limits (daily quota; not asked)

Gold SQL:
```sql
SELECT p.product_name FROM nw_order_details d
JOIN nw_products p ON p.product_id = d.product_id
GROUP BY p.product_id, p.product_name
ORDER BY SUM(d.unit_price * d.quantity * (1 - d.discount)) DESC LIMIT 3
```
InsightX SQL:
```sql
(none)
```

### nw_customers_no_orders (own_shop, hard)

- Question: How many Northwind customers have never placed an order?
- Status: ERROR; cause: pipeline_error
- Detail: All free AI models are busy or out of their free limits (daily quota; not asked)

Gold SQL:
```sql
SELECT COUNT(*) FROM nw_customers
WHERE customer_id NOT IN (SELECT customer_id FROM nw_orders WHERE customer_id IS NOT NULL)
```
InsightX SQL:
```sql
(none)
```

### nw_suppliers_usa (own_shop, easy)

- Question: How many Northwind suppliers are based in the USA?
- Status: ERROR; cause: pipeline_error
- Detail: All free AI models are busy or out of their free limits (daily quota; not asked)

Gold SQL:
```sql
SELECT COUNT(*) FROM nw_suppliers WHERE country = 'USA'
```
InsightX SQL:
```sql
(none)
```

### nw_top_category_revenue (own_shop, extra)

- Question: Which product category has the highest sales revenue after discount?
- Status: ERROR; cause: pipeline_error
- Detail: All free AI models are busy or out of their free limits (daily quota; not asked)

Gold SQL:
```sql
SELECT c.category_name FROM nw_order_details d
JOIN nw_products p ON p.product_id = d.product_id
JOIN nw_categories c ON c.category_id = p.category_id
GROUP BY c.category_id, c.category_name
ORDER BY SUM(d.unit_price * d.quantity * (1 - d.discount)) DESC LIMIT 1
```
InsightX SQL:
```sql
(none)
```
