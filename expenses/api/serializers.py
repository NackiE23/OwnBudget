from rest_framework import serializers

from expenses.models import OperationType

MAX_CATEGORIES = 500
MAX_OPERATIONS = 500
MAX_DELETIONS = 1000
# Сума в копійках: до 10 мільярдів гривень, щоб поміститися в BigInteger із запасом.
MAX_AMOUNT_MINOR = 10**12


class CategorySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    parent_id = serializers.UUIDField(allow_null=True, default=None)
    name = serializers.CharField(max_length=100)
    type = serializers.ChoiceField(choices=OperationType.choices)
    archived = serializers.BooleanField(default=False)
    sort_order = serializers.IntegerField(default=0)


class OperationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    type = serializers.ChoiceField(choices=OperationType.choices)
    amount_minor = serializers.IntegerField(min_value=1, max_value=MAX_AMOUNT_MINOR)
    category_id = serializers.UUIDField()
    date = serializers.DateField()
    description = serializers.CharField(max_length=500, allow_blank=True, allow_null=True, default="", trim_whitespace=True)
    created_at = serializers.IntegerField(min_value=0, help_text="Epoch, мілісекунди.")
    updated_at = serializers.IntegerField(min_value=0, help_text="Epoch, мілісекунди.")


class DeletionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    deleted_at = serializers.IntegerField(min_value=0, help_text="Epoch, мілісекунди.")


def _unique(items: list[dict], what: str) -> None:
    ids = [item["id"] for item in items]
    if len(ids) != len(set(ids)):
        raise serializers.ValidationError(f"{what}: id повторюються.")


class SyncRequestSerializer(serializers.Serializer):
    batch_id = serializers.UUIDField()
    app_version = serializers.CharField(max_length=32, allow_blank=True, default="")
    categories = CategorySerializer(many=True, max_length=MAX_CATEGORIES, allow_empty=True)
    operations = OperationSerializer(many=True, max_length=MAX_OPERATIONS, default=list)
    deleted_operations = DeletionSerializer(many=True, max_length=MAX_DELETIONS, default=list)

    def validate(self, data):
        _unique(data["categories"], "categories")
        _unique(data["operations"], "operations")
        _unique(data["deleted_operations"], "deleted_operations")

        categories = {item["id"]: item for item in data["categories"]}
        errors = []
        for item in data["categories"]:
            parent = categories.get(item["parent_id"]) if item["parent_id"] else None
            if item["parent_id"] and parent is None:
                errors.append(f"Категорія {item['id']}: батьківської {item['parent_id']} немає в categories.")
            elif parent and parent["parent_id"]:
                errors.append(f"Категорія {item['id']}: підкатегорії мають лише один рівень.")
        for item in data["operations"]:
            if item["category_id"] not in categories:
                errors.append(f"Операція {item['id']}: категорії {item['category_id']} немає в categories.")
        if errors:
            raise serializers.ValidationError(errors)
        return data
