from app.pipeline.pii import RedactionContext, get_redactor, spoken_to_digits


def redact(text, ctx=None):
    return get_redactor("regex").redact_text(text, 1, ctx or RedactionContext())


def test_numbers_by_context_and_length():
    text, ents = redact("My account number is 55821934 and my mobile is +91 98765 43210")
    assert text == "My account number is [ACCOUNT_NO_1] and my mobile is [PHONE_1]"
    assert {e.value for e in ents} == {"55821934", "+91 98765 43210"}


def test_card_read_aloud():
    text, _ = redact("the card is four one one one two two two two three three three three four four four four")
    assert text == "the card is [CARD_NUMBER_1]"


def test_spoken_double():
    assert spoken_to_digits("double five one two") == "5512"


def test_amounts_years_and_durations_are_not_pii():
    text, ents = redact("The bill was $45.99 in 2023, refund in 7-10 business days")
    assert ents == []


def test_same_value_same_placeholder_across_turns():
    ctx = RedactionContext()
    a, _ = redact("account 55821934", ctx)
    b, _ = redact("yes 55821934 is right, my account", ctx)
    assert "[ACCOUNT_NO_1]" in a and "[ACCOUNT_NO_1]" in b


def test_email_name_address_date():
    text, _ = redact("My name is Priya Sharma, email priya@x.com, I live at 221 Baker Street, DOB 12/05/1990")
    assert "Priya" not in text and "priya@x.com" not in text and "Baker" not in text and "1990" not in text


def test_name_memory_reuses_placeholder_later_in_call():
    ctx = RedactionContext()
    a, _ = redact("Hi, my name is James Wilson", ctx)
    b, _ = redact("Thanks, James. Mr Wilson, your plan is updated", ctx)
    assert a == "Hi, my name is [PERSON_1]" and "James" not in b and "Wilson" not in b
    assert b.count("[PERSON_1]") == 2


def test_name_cues_without_my_and_hinglish():
    for text in ("Yes, it is Anjali Nair, my phone", "main Anjali Brown bol rahi hoon", "Karan Das, account 12345678"):
        out, _ = redact(text)
        assert "Anjali" not in out and "Karan" not in out, out


def test_company_name_is_not_a_person():
    out, _ = redact("Thank you for calling Nimbus Telecom, this is Ravi")
    assert "Nimbus Telecom" in out and "Ravi" not in out


def test_vocative_and_greeting_cues():
    for text in ("I understand these issues, Sneha. I can help", "Thank you, Emily Gupta. I see it",
                 "Thanks Fatima. Let me check", "im Fatima Iyer and my number"):
        out, _ = redact(text)
        assert not any(n in out for n in ("Sneha", "Emily", "Fatima")), out


def test_vocative_does_not_eat_common_words():
    for text in ("Sure, Monday. See you", "Yes, Okay.", "Thank you for calling, Nimbus Telecom."):
        out, ents = redact(text)
        assert ents == [], (text, out)


def test_greeting_before_a_known_name_keeps_the_same_placeholder():
    ctx = RedactionContext()
    redact("Hi! Welcome to Nimbus Telecom. I'm Lucy.", ctx)
    text, _ = redact("Hey Lucy, my internet keeps dropping.", ctx)
    assert text == "Hey [PERSON_1], my internet keeps dropping."


def test_time_of_day_greetings_and_okay_are_name_cues():
    assert redact("Good morning Ravi, the bill is wrong.")[0] == "Good morning [PERSON_1], the bill is wrong."
    assert redact("Okay Sneha, done.")[0] == "Okay [PERSON_1], done."


def test_title_before_a_name_redacts_the_surname_not_the_title():
    assert redact("Thank you, Mr. Mehta. I see your address.")[0] == "Thank you, Mr. [PERSON_1]. I see your address."
    assert redact("Hello Dr. Rao, how can I help?")[0] == "Hello Dr. [PERSON_1], how can I help?"
    ctx = RedactionContext()
    redact("My name is Vivek Johnson.", ctx)
    assert redact("Thank you, Mr. Johnson.", ctx)[0] == "Thank you, Mr. [PERSON_1]."
