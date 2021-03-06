from django import forms
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.urlresolvers import reverse
from django.db import models
from django.db.models import Max
from ordered_model.models import OrderedModel

from lpd.constants import QuestionTypes, UnknownQuestionTypeError


class LearnerProfileDashboard(models.Model):
    name = models.TextField(help_text='Name of this LPD instance')
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    modified_at = models.DateTimeField(auto_now=True, editable=False)

    def __unicode__(self):
        return self.name

    def __str__(self):
        return unicode(self).encode('utf-8')

    def get_absolute_url(self):
        return reverse('lpd:view', kwargs=dict(pk=self.id))


class LearnerProfileDashboardForm(forms.ModelForm):
    class Meta:
        model = LearnerProfileDashboard
        fields = ['name']


class Section(OrderedModel):
    """
    Groups a set of related questions on the LPD.
    """
    lpd = models.ForeignKey(
        'LearnerProfileDashboard',
        related_name='sections',
    )
    title = models.CharField(
        max_length=120,
        blank=True,
        null=True,
        help_text='Text to display above questions belonging to this section (optional).',
    )

    order_with_respect_to = 'lpd'

    class Meta(OrderedModel.Meta):
        pass

    def __unicode__(self):
        return 'Section {id}: {title}'.format(id=self.id, title=self.title or '<title not set>')

    @property
    def questions(self):
        """
        Return list of all questions belonging to this section, irrespective of their type.
        """
        return sorted(
            list(self.qualitativequestion_set.all()) +
            list(self.multiplechoicequestion_set.all()) +
            list(self.rankingquestion_set.all()) +
            list(self.likertscalequestion_set.all()),
            key=lambda q: q.number
        )


class Question(models.Model):
    """
    Abstract base class for models representing learner profile question.
    """
    section = models.ForeignKey('Section')
    number = models.PositiveIntegerField(
        default=1,
        help_text='Number of this question relative to parent section.'
    )
    question_text = models.TextField(
        help_text='Text to display above answer options (if any) and input fields.',
    )
    notes = models.TextField(
        blank=True,
        null=True,
        help_text='Author notes about this question (optional).',
    )

    class Meta:
        abstract = True

    @property
    def type(self):
        """
        Return string that specifies the exact type of this question.
        """
        raise NotImplementedError

    @property
    def section_number(self):
        """
        Return string of the form 'X.Y'
        where X represents `order` of parent section and Y represents `number` of this question.
        """
        return '{section}.{number}'.format(section=self.section.order, number=self.number)


class QualitativeQuestion(Question):
    """
    Represents a questions that requires a free-text answer.
    """
    QUESTION_TYPES = (
        (QuestionTypes.SHORT_ANSWER, 'short answer'),
        (QuestionTypes.ESSAY, 'essay'),
    )
    question_type = models.CharField(
        choices=QUESTION_TYPES,
        max_length=20,
        help_text='Whether this question requires learners to produce a short answer or an essay.',
    )
    influences_group_membership = models.BooleanField(
        default=False,
        help_text=(
            'Whether answers to this question should be taken into account '
            'when calculating group membership for specific learners.'
        ),
    )

    def __unicode__(self):
        return 'QualitativeQuestion {id}: {text}'.format(id=self.id, text=self.question_text)

    @property
    def type(self):
        """
        Return string that specifies the exact type of this question.
        """
        return self.question_type

    def get_answer(self, learner):
        """
        Return answer that `learner` provided for this question.
        """
        try:
            answer = QualitativeAnswer.objects.get(question=self, learner=learner)
        except QualitativeAnswer.DoesNotExist:
            return ''
        else:
            return answer.text


class QuantitativeQuestion(Question):
    """
    Abstract base class for models representing questions with a pre-defined set of answer options.
    """
    answer_options = GenericRelation('AnswerOption')
    randomize_options = models.BooleanField(
        default=False,
        help_text='Whether to display answer options in random order on LPD.',
    )

    class Meta:
        abstract = True

    def get_content_type(self):
        return ContentType.objects.get_for_model(self).id

    @property
    def type(self):
        """
        Return string that specifies the exact type of this question.
        """
        raise NotImplementedError

    @classmethod
    def get_score(cls, question_type, value):
        """
        Return score to store for a given learner and knowledge component, based on `question_type` and `value`.

        Transformations applied to `value` are specific to the type of question that this is called on,
        so we don't provide a default implementation here.
        """
        if question_type in QuestionTypes.get_multiple_choice_types():
            return MultipleChoiceQuestion._get_score(value)
        elif question_type == QuestionTypes.RANKING:
            return RankingQuestion._get_score(value)
        elif question_type == QuestionTypes.LIKERT:
            return LikertScaleQuestion._get_score(value)
        else:
            raise UnknownQuestionTypeError(question_type)

    def get_answer_options(self):
        """
        Return answer options belonging to this question, sorted according to value of `randomize_options`:

        - If `randomize_options` is True, return answer options in random order.
        - If `randomize_options` is False, return answer options in alphabetical order (based on `option_text`).
        """
        ordering = '?' if self.randomize_options else 'option_text'
        return self.answer_options.order_by(ordering)

    @classmethod
    def get_value(cls, question_type, raw_value):
        """
        Return value to store for a given answer option, based on `question_type` and `raw_value`.

        Notes:

        - Answer options for multiple choice questions are either selected or unselected,
          which means we always have a meaningful value, and can return that value unchanged.
        - Answer options for ranking questions are either ranked or unranked.
          - If learner ranked an option, `raw_value` will be equal to the chosen rank.
          - If learner did not rank an option, `raw_value` will be `None`.
            In this case we return the default value for unranked option values.
            (By not ranking an option the learner indicates that the option is less important
            than the option with the lowest rank.)
        - Answer options for Likert scale questions are either ranked or unranked.
          - If learner ranked an option, `raw_value` will be equal to the chosen rank.
          - If learner did not rank an option, `raw_value` will be `None`.
            (By not ranking an option the learner isn't making a specific statement,
            so we simply consider it unanswered.)
        """
        if question_type == QuestionTypes.RANKING:
            if raw_value is None:
                return RankingQuestion.unranked_option_value()
        return raw_value


class MultipleChoiceQuestion(QuantitativeQuestion):
    """
    Represents a multiple choice question (MCQ) or multiple response question (MRQ).
    """
    max_options_to_select = models.PositiveIntegerField(
        default=1,
        help_text=(
            'Maximum number of answer options that learner is allowed to select for this question. '
            'Set this to 1 to create a multiple choice question. '
            'Set this to a value larger than one to create a multiple response question.'
        ),
    )

    def __unicode__(self):
        return 'MultipleChoiceQuestion {id}: {text}'.format(id=self.id, text=self.question_text)

    @property
    def type(self):
        """
        Return string that specifies the exact type of this question.
        """
        return QuestionTypes.MCQ if self.max_options_to_select == 1 else QuestionTypes.MRQ

    @classmethod
    def _get_score(cls, value):
        """
        Return score corresponding to `value`.

        For answer options belonging to multiple choice questions,
        `value` will be 0 (if learner did not select answer option)
        or 1 (if learner selected answer option).

        To produce a value that will have the desired effect on recommendations
        generated by the adaptive engine, we simply need to invert `value`:

        - If a learner selected a given option (i.e., if `value` is 1),
          this means that the adaptive engine should recommend content associated with that option.
        - Similarly, if a learner did not select a given option (i.e., if `value` is 0),
          this means that the adaptive engine should *not* recommend content associated with that option.
        - By default, the adaptive engine treats values associated with knowledge components
          as indicators for a learner's mastery level of these knowledge components.
        - If a learner's mastery level for a given knowledge component is low,
          the adaptive engine will recommend content associated with that knowledge component
          to give the learner a chance to improve their mastery level.

        So if a learner signals interest in a specific topic by selecting an answer option
        belonging to a multiple choice question, we want to store and send a low value
        for that answer option's knowledge component, and vice versa.
        """
        assert value == 0 or value == 1
        return value ^ 1


class RankingQuestion(QuantitativeQuestion):
    """
    Represents a question that asks learners to rank (a subset of) its answer options,
    on a scale from 1 to `number_of_options_to_rank`.
    """
    number_of_options_to_rank = models.PositiveIntegerField(
        default=3,
        help_text='Number of answer options belonging to this question that learners are allowed to rank.',
    )

    def __unicode__(self):
        return 'RankingQuestion {id}: {text}'.format(id=self.id, text=self.question_text)

    @property
    def type(self):
        """
        Return string that specifies the exact type of this question.
        """
        return QuestionTypes.RANKING

    @classmethod
    def unranked_option_value(cls):
        """
        Return value to store for answer options that a learner did not rank.
        """
        max_rank = cls.objects.all().aggregate(
            Max('number_of_options_to_rank')
        ).get('number_of_options_to_rank__max')
        return max_rank + 1

    @classmethod
    def _get_score(cls, value):
        """
        Return score corresponding to `value`.

        If learner ranked an answer option belonging to a ranking question,
        `value` will be equal to the chosen rank.

        If learner did not rank the answer option,
        `value` will be equal to `RankingQuestion.unranked_option_value`
        i.e., it will be an arbitrary number that is greater than any of the ranks
        that the learner can choose from for the given answer option.
        (Note that this number is computed to be the same across ranking questions
        to make sure that values scale consistently.)

        To produce a value that will have the desired effect on recommendations
        generated by the adaptive engine, rescale value x to lie in [0,1]
        by doing

        x:=(x-m)/(M-m)

        where m is the theoretical minimum for x and M is the theoretical maximum for x.
        For example, if a ranking question let learners choose between values from 1 to 5
        for its answer options, and there were no ranking questions with ranks higher than 5:

        - m would be 1.
        - M would be 6 (to account for unranked options).
        - The value to send to the adaptive engine would be calculated as x:=(x-1)/(6-1).

        Note that unlike for multiple choice questions, `_get_score` should not invert values:
        Lower ranks represent higher priority.
        """
        theoretical_min = 1.
        theoretical_max = cls.unranked_option_value()
        return (value - theoretical_min) / (theoretical_max - theoretical_min)


class LikertScaleQuestion(QuantitativeQuestion):
    """
    Represents a (simplified) Likert Scale question, cf. https://en.wikipedia.org/wiki/Likert_scale.
    """
    answer_option_range = models.PositiveIntegerField(
        default=5,
        help_text=(
            'Number of values that learners can choose from for each answer option. '
            'For example, to create 5-point scale, set this to 5.'
        ),
    )
    range_min_text = models.CharField(
        max_length=50,
        default='strongly disagree',
        help_text='Meaning of lowest value of Likert scale. For example: "Not very valuable".',
    )
    range_max_text = models.CharField(
        max_length=50,
        default='strongly agree',
        help_text='Meaning of highest value of Likert scale. For example: "Extremely valuable."',
    )

    def __unicode__(self):
        return 'LikertScaleQuestion {id}: {text}'.format(id=self.id, text=self.question_text)

    @property
    def type(self):
        """
        Return string that specifies the exact type of this question.
        """
        return QuestionTypes.LIKERT

    @classmethod
    def _get_score(cls, value):
        """
        Return score corresponding to `value`.

        Likert scale questions will not influence recommendations in 2018 version of HPL course,
        so this remains a stub for now.
        """
        raise NotImplementedError


class AnswerOption(models.Model):
    """
    Represents a specific answer option for a quantitative learner profile question.
    """
    # Use generic relation to connect this model to QuantitativeQuestion (which is abstract).
    content_type = models.ForeignKey(ContentType)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')

    knowledge_component = models.OneToOneField(
        'KnowledgeComponent',
        blank=True,
        null=True,
        help_text='Knowledge component that this answer option is associated with.',
        related_name='answer_option',
    )
    option_text = models.TextField(
        help_text='Text to display for this answer option.',
    )
    allows_custom_input = models.BooleanField(
        default=False,
        help_text=(
            'Whether this option allows learners to specify custom input. '
            'For example, a quantitative question might include an "Other" option '
            'that allows learners to specify what that option represents. '
            'The LPD will render this as: '
            'Other: ______________.'
        ),
    )
    influences_recommendations = models.BooleanField(
        default=False,
        help_text=(
            'Whether answers to this answer option '
            'should be sent to the adaptive engine to tune recommendations.'
        ),
    )

    class Meta:
        ordering = ["-option_text"]

    def __unicode__(self):
        return 'AnswerOption {id}: {text}'.format(id=self.id, text=self.option_text)

    def get_data(self, learner):
        """
        Return value that `learner` chose for this answer option.

        If `answer_option` belongs to a multiple choice question,
        the value returned will be 1 if the learner selected the answer option,
        and 0 if the learner did not select the answer option.
        """
        try:
            answer = QuantitativeAnswer.objects.get(answer_option=self, learner=learner)
        except QuantitativeAnswer.DoesNotExist:
            return None
        else:
            return {
                'value': answer.value,
                'custom_input': answer.custom_input or ''
            }


class Answer(models.Model):
    """
    Abstract base class for models representing learner answers to LPD questions.
    """
    learner = models.ForeignKey(settings.AUTH_USER_MODEL)

    class Meta:
        abstract = True


class QualitativeAnswer(Answer):
    """
    Represents a learner's answer to a qualitative question.
    """
    question = models.ForeignKey(
        'QualitativeQuestion',
        related_name='learner_answers',
    )
    text = models.TextField(
        help_text='Answer that the learner provided to the associated question.',
    )

    def __unicode__(self):
        return 'QualitativeAnswer {id}: {text}'.format(id=self.id, text=self.text)


class QuantitativeAnswer(Answer):
    """
    Represents a learner's answer to a specific answer option of a quantitative question.
    """
    answer_option = models.ForeignKey(
        'AnswerOption',
        related_name='learner_answers',
    )
    value = models.PositiveIntegerField(
        help_text=(
            'The value that the learner chose for the associated answer option. '
            'Note that if the answer option belongs to a multiple choice question, '
            'this field will be set to 1 if the learner selected the answer option, '
            'and to 0 if the learner did not select the answer option. '
            'For ranking and Likert scale questions, this field will be set to the value '
            'that the learner chose from the range of available values.'
        ),
    )
    custom_input = models.CharField(
        max_length=120,
        blank=True,
        null=True,
        help_text='The input that a learner provided for a quantitative question that `allows_custom_input`.',
    )

    def __unicode__(self):
        return 'QuantitativeAnswer {id}: {value}'.format(id=self.id, value=self.value)


class KnowledgeComponent(models.Model):
    """
    Represents a knowledge component (tag) that the adaptive engine uses to track topic mastery.

    A knowledge component is either associated with a specific answer option,
    or represents a 'group' that a learner might be associated with.
    """
    kc_id = models.CharField(
        max_length=50,
        help_text='String that LPD and adaptive engine use to uniquely identify this knowledge component.',
    )
    kc_name = models.CharField(
        max_length=100,
        help_text='Verbose name for this knowledge component.',
    )

    def __unicode__(self):
        return 'KnowledgeComponent {id}: {kc_id}, {kc_name}'.format(id=self.id, kc_id=self.kc_id, kc_name=self.kc_name)


class Score(models.Model):
    """
    Represents a learner's score for a specific knowledge node.

    For a knowledge component that represents a group,
    the score is equal to the probability of the learner belonging to that group.

    For a knowledge component that is associated with an answer option,
    the score represents the transformed value of the learner's answer to that answer option.
    """
    knowledge_component = models.ForeignKey('KnowledgeComponent')
    learner = models.ForeignKey(settings.AUTH_USER_MODEL)
    value = models.FloatField(
        help_text="The learner's score for the associated knowledge component.",
    )

    def __unicode__(self):
        return 'Score {id}: {value}'.format(id=self.id, value=self.value)
