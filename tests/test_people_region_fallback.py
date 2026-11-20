"""A backlit silhouette is the same black as the ground it stands on, so SAM returns person and ground together and finds no one.
The scene model's people region then gives the people: each separate part of it is one (a person's pieces kept together)."""
import numpy as np

from image_triage import people_instances


def test_each_part_of_the_people_region_is_a_person():
    people = np.zeros((400, 600), bool)
    people[60:200, 480:520] = True                  # a photographer
    people[205:240, 488:512] = True                 # their legs, a few pixels below: the same person
    people[100:170, 50:70] = True                   # someone else
    people[10:12, 10:12] = True                     # a speck
    found = people_instances._people_from_region(people, float(people.size))
    assert len(found) == 2
    first = found[0]
    x0, y0, x1, y1 = first.bounds
    assert (x0, y0) == (480, 60) and y1 >= 239                              # body and legs, one person, largest first
    sx, sy = first.seed_norm
    assert people[int(sy * 400), int(sx * 600)]                               # its seed is on the person


def test_nothing_in_the_region_is_nobody():
    assert people_instances._people_from_region(np.zeros((100, 100), bool), 10000.0) == []
